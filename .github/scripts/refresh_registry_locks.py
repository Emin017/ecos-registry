#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from registry_schema import (
    GITHUB_BRANCH_UPDATE_SOURCE_TYPE,
    github_repository_from_homepage,
    has_remote_lock_source,
    iter_registry_platforms,
)


SHA256_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
GIT_COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}")
URL_TIMEOUT_SECONDS = 30.0

JsonFetcher = Callable[[str], dict[str, Any]]
TextFetcher = Callable[[str], str]
SizeFetcher = Callable[[str], int]
ArchiveLockFetcher = Callable[[str], tuple[str, int]]


@dataclass
class RefreshResult:
    updates: list[str]
    failures: list[str]


def refresh_registry_file(
    path: Path,
    *,
    json_fetcher: JsonFetcher | None = None,
    text_fetcher: TextFetcher | None = None,
    size_fetcher: SizeFetcher | None = None,
    archive_lock_fetcher: ArchiveLockFetcher | None = None,
) -> RefreshResult:
    data = json.loads(path.read_text(encoding="utf-8"))
    result = refresh_registry_data(
        data,
        json_fetcher=json_fetcher,
        text_fetcher=text_fetcher,
        size_fetcher=size_fetcher,
        archive_lock_fetcher=archive_lock_fetcher,
    )
    if result.updates:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for update in result.updates:
        print(update)
    for failure in result.failures:
        print(failure, file=sys.stderr)
    print(f"refreshed {len(result.updates)} platform lock field set(s)")
    return result


def refresh_registry_data(
    data: dict[str, Any],
    *,
    json_fetcher: JsonFetcher | None = None,
    text_fetcher: TextFetcher | None = None,
    size_fetcher: SizeFetcher | None = None,
    archive_lock_fetcher: ArchiveLockFetcher | None = None,
) -> RefreshResult:
    json_fetcher = json_fetcher or fetch_json_url
    text_fetcher = text_fetcher or fetch_text_url
    size_fetcher = size_fetcher or fetch_url_size
    archive_lock_fetcher = archive_lock_fetcher or fetch_archive_lock
    updates: list[str] = []
    failures: list[str] = []
    for platform_entry in iter_registry_platforms(data):
        platform = platform_entry.value
        try:
            if "update_source" in platform:
                updates.extend(
                    refresh_github_branch_platform(
                        platform,
                        path=platform_entry.path,
                        homepage=platform_entry.homepage,
                        json_fetcher=json_fetcher,
                        archive_lock_fetcher=archive_lock_fetcher,
                    )
                )
                continue
            if not has_remote_lock_source(platform):
                continue
            updates.extend(
                refresh_platform_lock(
                    platform,
                    path=platform_entry.path,
                    json_fetcher=json_fetcher,
                    text_fetcher=text_fetcher,
                    size_fetcher=size_fetcher,
                )
            )
        except Exception as exc:
            failures.append(f"{platform_entry.path}: refresh failed: {exc}")
    return RefreshResult(updates=updates, failures=failures)


def refresh_github_branch_platform(
    platform: dict[str, Any],
    *,
    path: str,
    homepage: str | None,
    json_fetcher: JsonFetcher,
    archive_lock_fetcher: ArchiveLockFetcher,
) -> list[str]:
    source = platform.get("update_source")
    if not isinstance(source, dict):
        raise RuntimeError("update_source must be an object")

    source_type = source.get("type")
    if source_type != GITHUB_BRANCH_UPDATE_SOURCE_TYPE:
        raise RuntimeError(
            f"update_source.type must equal '{GITHUB_BRANCH_UPDATE_SOURCE_TYPE}'"
        )

    repository = github_repository_from_homepage(homepage)
    if repository is None:
        raise RuntimeError(
            "github_branch requires the entry homepage to be a canonical GitHub repository URL"
        )

    branch = source.get("branch")
    if not isinstance(branch, str) or not branch or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in branch
    ):
        raise RuntimeError(
            "update_source.branch must be a non-empty branch name without whitespace or control characters"
        )

    head_url = f"https://api.github.com/repos/{repository}/commits/{quote(branch, safe='')}"
    metadata = json_fetcher(head_url)
    commit = metadata.get("sha")
    if not isinstance(commit, str) or GIT_COMMIT_RE.fullmatch(commit) is None:
        raise RuntimeError("GitHub branch response did not contain a 40-character commit SHA")
    commit = commit.lower()

    archive_url = f"https://github.com/{repository}/archive/{commit}.tar.gz"
    archive_prefix = f"{repository.rsplit('/', 1)[1]}-{commit}"
    needs_archive_lock = (
        platform.get("url") != archive_url
        or not isinstance(platform.get("sha256"), str)
        or SHA256_RE.fullmatch(platform["sha256"]) is None
        or not isinstance(platform.get("size"), int)
        or isinstance(platform.get("size"), bool)
        or platform["size"] <= 0
    )

    sha256: str | None = None
    size: int | None = None
    if needs_archive_lock:
        sha256, size = archive_lock_fetcher(archive_url)
        if SHA256_RE.fullmatch(sha256) is None:
            raise RuntimeError(f"{path}: could not resolve a valid sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise RuntimeError(f"{path}: could not resolve a positive size")

    updates: list[str] = []
    if platform.get("url") != archive_url:
        platform["url"] = archive_url
        updates.append(f"{path}.url refreshed")
    if sha256 is not None and platform.get("sha256") != sha256.lower():
        platform["sha256"] = sha256.lower()
        updates.append(f"{path}.sha256 refreshed")
    if size is not None and platform.get("size") != size:
        platform["size"] = size
        updates.append(f"{path}.size refreshed")
    if platform.get("strip_prefix") != archive_prefix:
        platform["strip_prefix"] = archive_prefix
        updates.append(f"{path}.strip_prefix refreshed")
    return updates


def refresh_platform_lock(
    platform: dict[str, Any],
    *,
    path: str,
    json_fetcher: JsonFetcher,
    text_fetcher: TextFetcher,
    size_fetcher: SizeFetcher,
) -> list[str]:
    updates: list[str] = []
    metadata = fetch_metadata(platform, json_fetcher)
    sha256 = metadata.get("sha256")
    size = metadata.get("size")

    if not isinstance(sha256, str):
        sha256_url = platform.get("sha256_url")
        if isinstance(sha256_url, str) and sha256_url:
            sha256 = parse_sha256_text(text_fetcher(sha256_url))

    if not isinstance(size, int) or size <= 0:
        url = platform.get("url")
        if isinstance(url, str) and url:
            size = size_fetcher(url)

    if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
        raise RuntimeError(f"{path}: could not resolve a valid sha256")
    if not isinstance(size, int) or size <= 0:
        raise RuntimeError(f"{path}: could not resolve a positive size")

    normalized_sha = sha256.lower()
    if platform.get("sha256") != normalized_sha:
        platform["sha256"] = normalized_sha
        updates.append(f"{path}.sha256 refreshed")
    if platform.get("size") != size:
        platform["size"] = size
        updates.append(f"{path}.size refreshed")
    return updates


def fetch_metadata(platform: dict[str, Any], json_fetcher: JsonFetcher) -> dict[str, Any]:
    metadata_url = platform.get("metadata_url")
    if not isinstance(metadata_url, str) or not metadata_url:
        return {}
    metadata = json_fetcher(metadata_url)
    return {
        "sha256": metadata.get("sha256"),
        "size": metadata.get("size"),
    }


def fetch_json_url(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=URL_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text_url(url: str) -> str:
    with urlopen(url, timeout=URL_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8")


def fetch_url_size(url: str) -> int:
    request = Request(url, method="HEAD")
    try:
        with urlopen(request, timeout=URL_TIMEOUT_SECONDS) as response:
            length = response.headers.get("Content-Length")
            if length is not None:
                return int(length)
    except HTTPError as error:
        if error.code not in (405, 501):
            raise

    request = Request(url, headers={"Range": "bytes=0-0"})
    with urlopen(request, timeout=URL_TIMEOUT_SECONDS) as response:
        content_range = response.headers.get("Content-Range")
        if content_range and "/" in content_range:
            return int(content_range.rsplit("/", 1)[1])
        length = response.headers.get("Content-Length")
        if length is not None:
            return int(length)
    raise RuntimeError(f"could not resolve size for {url}")


def fetch_archive_lock(url: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with urlopen(url, timeout=URL_TIMEOUT_SECONDS) as response:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    if size <= 0:
        raise RuntimeError(f"could not resolve a positive size for {url}")
    return digest.hexdigest(), size


def parse_sha256_text(text: str) -> str:
    match = SHA256_RE.search(text)
    if match is None:
        raise RuntimeError("sha256 sidecar does not contain a 64-character hex hash")
    return match.group(0).lower()


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh static sha256/size lock fields in tool-registry.json")
    parser.add_argument("registry", nargs="?", default="tool-registry.json", type=Path)
    args = parser.parse_args()
    result = refresh_registry_file(args.registry)
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
