#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import ntpath
import posixpath
import re
import socket
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from http.client import HTTPException
from pathlib import Path
from typing import Any, Protocol, TypeGuard, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ALLOWED_TOP_LEVEL_KEYS = frozenset(("schema_version", "tools", "pdks"))
TOOL_REQUIRED_FIELDS = (
    "name",
    "display_name",
    "description",
    "category",
    "homepage",
    "versions",
)
PDK_REQUIRED_FIELDS = (
    "id",
    "display_name",
    "description",
    "category",
    "homepage",
    "versions",
)
VERSION_REQUIRED_FIELDS = ("version", "platforms")
PLATFORM_REQUIRED_FIELDS = ("url", "sha256", "size")
ALLOWED_PLATFORM_FIELDS = frozenset(
    PLATFORM_REQUIRED_FIELDS + ("strip_prefix", "post_install")
)
ARCHIVE_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".zip")
IDENTIFIER_RE = re.compile(r"^[a-z0-9_-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUMERIC_VERSION_RE = re.compile(r"^\d+(?:\.\d+)+$")
URL_TIMEOUT_SECONDS = 5.0


class Rule(StrEnum):
    """Stable machine-readable identifier for each registry spec clause.

    Tests assert on these; message wording is free to change.
    """

    # Generic shape checks.
    INVALID_JSON = "invalid-json"
    EXPECTED_OBJECT = "expected-object"
    EXPECTED_ARRAY = "expected-array"
    EXPECTED_STRING = "expected-string"
    EMPTY_STRING = "empty-string"
    NON_EMPTY_ARRAY = "non-empty-array"
    NON_EMPTY_OBJECT = "non-empty-object"
    NON_EMPTY_STRING = "non-empty-string"
    REQUIRED = "required"
    POSITIVE_INTEGER = "positive-integer"

    # Registry-specific semantic rules.
    SCHEMA_VERSION = "schema-version"
    UNKNOWN_TOP_LEVEL_KEY = "unknown-top-level-key"
    IDENTIFIER_EMPTY = "identifier-empty"
    IDENTIFIER_FORMAT = "identifier-format"
    DUPLICATE_ID = "duplicate-id"
    DUPLICATE_VERSION = "duplicate-version"
    VERSION_ORDER = "version-order"
    MIXED_VERSION_FORMAT = "mixed-version-format"
    EMPTY_PLATFORM_KEY = "empty-platform-key"
    ALL_PLATFORM_FOR_TOOL = "all-platform-for-tool"
    UNKNOWN_PLATFORM_FIELD = "unknown-platform-field"
    MALFORMED_URL = "malformed-url"
    URL_SCHEME = "url-scheme"
    URL_HOST = "url-host"
    ARCHIVE_SUFFIX = "archive-suffix"
    SHA256_FORMAT = "sha256-format"
    COMMAND_ARRAY = "command-array"
    CWD_RELATIVE = "cwd-relative"
    CWD_ESCAPE = "cwd-escape"
    URL_UNREACHABLE = "url-unreachable"


@dataclass(frozen=True)
class Issue:
    """One spec violation: where it occurred, which rule, human detail."""

    path: str
    rule: Rule
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class UrlResponse(Protocol):
    status: int

    def __enter__(self) -> UrlResponse: ...

    def __exit__(self, *args: object) -> None: ...

    def read(self, size: int | None = None) -> bytes: ...


@dataclass(frozen=True)
class AssetUrl:
    path: str
    url: str


UrlOpener = Callable[[Request, float], UrlResponse]
UrlChecker = Callable[[str], str | None]


def validate_registry(
    path: Path,
    *,
    check_urls: bool = False,
    url_checker: UrlChecker | None = None,
) -> list[Issue]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [Issue("$", Rule.INVALID_JSON, f"invalid JSON: {exc.msg}")]
    return validate_registry_data(data, check_urls=check_urls, url_checker=url_checker)


def validate_registry_data(
    data: object,
    *,
    check_urls: bool = False,
    url_checker: UrlChecker | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    registry = _as_str_dict(data)
    if registry is None:
        return [Issue("$", Rule.EXPECTED_OBJECT, "must be a JSON object")]

    asset_urls: list[AssetUrl] = []
    _validate_top_level(registry, issues)
    tools = _array_or_none(registry.get("tools"), "tools", issues)
    pdks = _array_or_none(registry.get("pdks"), "pdks", issues)

    if tools is not None:
        _validate_entries(
            tools,
            "tools",
            "tool",
            "name",
            TOOL_REQUIRED_FIELDS,
            issues,
            asset_urls,
        )
    if pdks is not None:
        _validate_entries(
            pdks,
            "pdks",
            "PDK",
            "id",
            PDK_REQUIRED_FIELDS,
            issues,
            asset_urls,
        )

    if check_urls:
        checker = url_checker or check_url_reachable
        for asset_url in asset_urls:
            error = checker(asset_url.url)
            if error is not None:
                issues.append(
                    Issue(
                        asset_url.path,
                        Rule.URL_UNREACHABLE,
                        f"URL check failed for {asset_url.url}: {error}",
                    )
                )

    return issues


def _validate_top_level(data: dict[str, Any], issues: list[Issue]) -> None:
    if data.get("schema_version") != 2:
        issues.append(Issue("schema_version", Rule.SCHEMA_VERSION, "must equal 2"))

    for key in data:
        if key not in ALLOWED_TOP_LEVEL_KEYS:
            issues.append(
                Issue(key, Rule.UNKNOWN_TOP_LEVEL_KEY, "unknown top-level key")
            )


def _array_or_none(
    value: object, path: str, issues: list[Issue]
) -> list[Any] | None:
    if not isinstance(value, list):
        issues.append(Issue(path, Rule.EXPECTED_ARRAY, "must be an array"))
        return None
    return value


def _validate_entries(
    entries: list[Any],
    collection_path: str,
    label: str,
    id_field: str,
    required_fields: tuple[str, ...],
    issues: list[Issue],
    asset_urls: list[AssetUrl],
) -> None:
    seen_ids: dict[str, str] = {}
    for index, raw_entry in enumerate(entries):
        entry_path = f"{collection_path}[{index}]"
        entry = _as_str_dict(raw_entry)
        if entry is None:
            issues.append(Issue(entry_path, Rule.EXPECTED_OBJECT, "must be an object"))
            continue

        _require_fields(entry, required_fields, entry_path, issues)
        _validate_string_fields(
            entry,
            ("display_name", "description", "category", "homepage"),
            entry_path,
            issues,
        )
        _validate_identifier(entry.get(id_field), f"{entry_path}.{id_field}", issues)
        identifier = entry.get(id_field)
        if isinstance(identifier, str) and IDENTIFIER_RE.fullmatch(identifier):
            if identifier in seen_ids:
                issues.append(
                    Issue(
                        f"{entry_path}.{id_field}",
                        Rule.DUPLICATE_ID,
                        f"duplicate {label} {id_field} {identifier!r}; "
                        f"first seen at {seen_ids[identifier]}",
                    )
                )
            else:
                seen_ids[identifier] = f"{entry_path}.{id_field}"

        versions = entry.get("versions")
        if not isinstance(versions, list) or not versions:
            issues.append(
                Issue(
                    f"{entry_path}.versions",
                    Rule.NON_EMPTY_ARRAY,
                    "must be a non-empty array",
                )
            )
            continue
        _validate_versions(
            versions,
            f"{entry_path}.versions",
            entry_type="tool" if collection_path == "tools" else "pdk",
            issues=issues,
            asset_urls=asset_urls,
        )


def _require_fields(
    entry: dict[str, Any],
    fields: tuple[str, ...],
    path: str,
    issues: list[Issue],
) -> None:
    for field in fields:
        if field not in entry:
            issues.append(
                Issue(f"{path}.{field}", Rule.REQUIRED, "missing required field")
            )


def _validate_string_fields(
    entry: dict[str, Any],
    fields: tuple[str, ...],
    path: str,
    issues: list[Issue],
) -> None:
    for field in fields:
        if field in entry and not _is_non_empty_string(entry[field]):
            issues.append(
                Issue(
                    f"{path}.{field}",
                    Rule.NON_EMPTY_STRING,
                    "must be a non-empty string",
                )
            )


def _validate_identifier(value: object, path: str, issues: list[Issue]) -> None:
    if not _is_non_empty_string(value):
        issues.append(
            Issue(path, Rule.IDENTIFIER_EMPTY, "must be a non-empty stable identifier")
        )
        return
    if not IDENTIFIER_RE.fullmatch(value):
        issues.append(Issue(path, Rule.IDENTIFIER_FORMAT, "must match ^[a-z0-9_-]+$"))


def _validate_versions(
    versions: list[Any],
    path: str,
    entry_type: str,
    issues: list[Issue],
    asset_urls: list[AssetUrl],
) -> None:
    seen_versions: dict[str, str] = {}
    version_values: list[str] = []

    for index, raw_version in enumerate(versions):
        version_path = f"{path}[{index}]"
        version = _as_str_dict(raw_version)
        if version is None:
            issues.append(
                Issue(version_path, Rule.EXPECTED_OBJECT, "must be an object")
            )
            continue

        _require_fields(version, VERSION_REQUIRED_FIELDS, version_path, issues)
        version_value = version.get("version")
        if not _is_non_empty_string(version_value):
            issues.append(
                Issue(
                    f"{version_path}.version",
                    Rule.NON_EMPTY_STRING,
                    "must be a non-empty string",
                )
            )
        else:
            version_values.append(version_value)
            if version_value in seen_versions:
                issues.append(
                    Issue(
                        f"{version_path}.version",
                        Rule.DUPLICATE_VERSION,
                        f"duplicate version {version_value!r}; "
                        f"first seen at {seen_versions[version_value]}",
                    )
                )
            else:
                seen_versions[version_value] = f"{version_path}.version"

        if "requires" in version and entry_type == "tool" and not isinstance(
            version["requires"], list
        ):
            issues.append(
                Issue(
                    f"{version_path}.requires",
                    Rule.EXPECTED_ARRAY,
                    "must be an array",
                )
            )

        platforms = version.get("platforms")
        if not isinstance(platforms, dict) or not platforms:
            issues.append(
                Issue(
                    f"{version_path}.platforms",
                    Rule.NON_EMPTY_OBJECT,
                    "must be a non-empty object",
                )
            )
            continue
        _validate_platforms(
            platforms,
            f"{version_path}.platforms",
            entry_type,
            issues,
            asset_urls,
        )

    _validate_version_order(version_values, path, issues)


def _validate_version_order(
    versions: list[str],
    path: str,
    issues: list[Issue],
) -> None:
    if len(versions) < 2:
        return

    parsed_dates = [_parse_date_version(version) for version in versions]
    parsed_numbers = [_parse_numeric_version(version) for version in versions]

    if all(parsed is not None for parsed in parsed_dates):
        _validate_descending(parsed_dates, path, issues)
        return

    if all(parsed is not None for parsed in parsed_numbers):
        _validate_descending(parsed_numbers, path, issues)
        return

    issues.append(
        Issue(
            path,
            Rule.MIXED_VERSION_FORMAT,
            "mixed or unsupported version format; use YYYY-MM-DD or dotted "
            "numeric versions and keep newest first",
        )
    )


def _parse_date_version(version: str) -> date | None:
    if not DATE_VERSION_RE.fullmatch(version):
        return None
    try:
        return date.fromisoformat(version)
    except ValueError:
        return None


def _parse_numeric_version(version: str) -> tuple[int, ...] | None:
    if not NUMERIC_VERSION_RE.fullmatch(version):
        return None
    return tuple(int(part) for part in version.split("."))


def _validate_descending(
    parsed_versions: Sequence[date | tuple[int, ...] | None],
    path: str,
    issues: list[Issue],
) -> None:
    comparable_versions = [
        version for version in parsed_versions if version is not None
    ]
    if comparable_versions != sorted(comparable_versions, reverse=True):
        issues.append(
            Issue(path, Rule.VERSION_ORDER, "newest version must appear first")
        )


def _validate_platforms(
    platforms: dict[str, Any],
    path: str,
    entry_type: str,
    issues: list[Issue],
    asset_urls: list[AssetUrl],
) -> None:
    for platform_key, raw_platform in platforms.items():
        if not _is_non_empty_string(platform_key):
            issues.append(
                Issue(
                    path,
                    Rule.EMPTY_PLATFORM_KEY,
                    "platform key must be non-empty",
                )
            )
            continue

        platform_path = f"{path}.{platform_key}"
        if entry_type == "tool" and platform_key == "all-platform":
            issues.append(
                Issue(
                    platform_path,
                    Rule.ALL_PLATFORM_FOR_TOOL,
                    "all-platform is not allowed for tools",
                )
            )

        platform = _as_str_dict(raw_platform)
        if platform is None:
            issues.append(
                Issue(platform_path, Rule.EXPECTED_OBJECT, "must be an object")
            )
            continue

        _require_fields(platform, PLATFORM_REQUIRED_FIELDS, platform_path, issues)
        for field in platform:
            if field not in ALLOWED_PLATFORM_FIELDS:
                issues.append(
                    Issue(
                        f"{platform_path}.{field}",
                        Rule.UNKNOWN_PLATFORM_FIELD,
                        "unknown platform field",
                    )
                )

        url_path = f"{platform_path}.url"
        if _validate_platform_url(platform.get("url"), url_path, issues):
            asset_urls.append(AssetUrl(path=url_path, url=platform["url"]))
        _validate_sha256(platform.get("sha256"), f"{platform_path}.sha256", issues)
        _validate_size(platform.get("size"), f"{platform_path}.size", issues)
        if "strip_prefix" in platform and not _is_non_empty_string(
            platform["strip_prefix"]
        ):
            issues.append(
                Issue(
                    f"{platform_path}.strip_prefix",
                    Rule.NON_EMPTY_STRING,
                    "must be a non-empty string",
                )
            )
        if "post_install" in platform:
            _validate_post_install(
                platform["post_install"],
                f"{platform_path}.post_install",
                issues,
            )


def _validate_platform_url(value: object, path: str, issues: list[Issue]) -> bool:
    if not _is_non_empty_string(value):
        issues.append(Issue(path, Rule.NON_EMPTY_STRING, "must be a non-empty string"))
        return False
    if _contains_url_control_character(value):
        issues.append(
            Issue(
                path,
                Rule.MALFORMED_URL,
                "malformed URL: must not contain whitespace or control characters",
            )
        )
        return False

    try:
        parsed = urlparse(value)
        _ = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        issues.append(Issue(path, Rule.MALFORMED_URL, f"malformed URL: {exc}"))
        return False

    valid = True
    if parsed.scheme not in ("http", "https"):
        issues.append(Issue(path, Rule.URL_SCHEME, "must use http or https"))
        valid = False
    if not parsed.netloc:
        issues.append(Issue(path, Rule.URL_HOST, "must include a host"))
        valid = False
    elif hostname is None:
        issues.append(
            Issue(
                path,
                Rule.MALFORMED_URL,
                "malformed URL: must include a valid host",
            )
        )
        valid = False
    if not parsed.path.lower().endswith(ARCHIVE_SUFFIXES):
        issues.append(Issue(path, Rule.ARCHIVE_SUFFIX, "unsupported archive suffix"))
        valid = False
    return valid


def _validate_sha256(value: object, path: str, issues: list[Issue]) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        issues.append(
            Issue(
                path,
                Rule.SHA256_FORMAT,
                "must be a lowercase 64-character hex string",
            )
        )


def _validate_size(value: object, path: str, issues: list[Issue]) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        issues.append(Issue(path, Rule.POSITIVE_INTEGER, "must be a positive integer"))


def _validate_post_install(value: object, path: str, issues: list[Issue]) -> None:
    if not isinstance(value, list):
        issues.append(Issue(path, Rule.EXPECTED_ARRAY, "must be an array"))
        return

    for index, raw_command in enumerate(value):
        command_path = f"{path}[{index}]"
        command = _as_str_dict(raw_command)
        if command is None:
            issues.append(
                Issue(command_path, Rule.EXPECTED_OBJECT, "must be an object")
            )
            continue
        if "command" not in command:
            issues.append(
                Issue(
                    f"{command_path}.command",
                    Rule.REQUIRED,
                    "missing required field",
                )
            )
        else:
            _validate_command_array(
                command["command"], f"{command_path}.command", issues
            )
        if "cwd" in command:
            problem = _post_install_cwd_problem(command["cwd"])
            if problem is not None:
                rule, message = problem
                issues.append(Issue(f"{command_path}.cwd", rule, message))


def _validate_command_array(value: object, path: str, issues: list[Issue]) -> None:
    if not isinstance(value, list) or not value:
        issues.append(
            Issue(path, Rule.COMMAND_ARRAY, "must be a non-empty string array")
        )
        return

    for index, part in enumerate(value):
        if not isinstance(part, str):
            issues.append(
                Issue(f"{path}[{index}]", Rule.EXPECTED_STRING, "must be a string")
            )
        elif not part:
            issues.append(
                Issue(f"{path}[{index}]", Rule.EMPTY_STRING, "must be non-empty")
            )


def _is_non_empty_string(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value)


def _as_str_dict(value: object) -> dict[str, Any] | None:
    """Narrow a decoded JSON node to a string-keyed dict.

    The cast is a runtime no-op; it gives type checkers a concrete dict type
    instead of the gradual type that bare ``isinstance(value, dict)`` produces.
    """
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _contains_url_control_character(value: str) -> bool:
    return any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)


def _post_install_cwd_problem(value: object) -> tuple[Rule, str] | None:
    if not isinstance(value, str) or not value:
        return (Rule.CWD_RELATIVE, "must be a non-empty relative path")
    if (
        value.startswith(("/", "\\"))
        or Path(value).is_absolute()
        or ntpath.isabs(value)
        or ntpath.splitdrive(value)[0]
    ):
        return (Rule.CWD_RELATIVE, "must be a non-empty relative path")
    normalized = posixpath.normpath(value.replace("\\", "/"))
    if normalized == ".." or normalized.startswith("../"):
        return (Rule.CWD_ESCAPE, "must stay inside the extracted resource")
    return None


def _open_url(request: Request, timeout: float) -> UrlResponse:
    return urlopen(request, timeout=timeout)


def check_url_reachable(
    url: str,
    *,
    opener: UrlOpener = _open_url,
    timeout: float = URL_TIMEOUT_SECONDS,
) -> str | None:
    head_error = _request_url(url, "HEAD", opener=opener, timeout=timeout)
    if head_error is None:
        return None
    return _request_url(
        url,
        "GET",
        opener=opener,
        timeout=timeout,
        headers={"Range": "bytes=0-0"},
        read_limit=1,
    )


def _request_url(
    url: str,
    method: str,
    *,
    opener: UrlOpener,
    timeout: float,
    headers: dict[str, str] | None = None,
    read_limit: int | None = None,
) -> str | None:
    try:
        request = Request(url, headers=headers or {}, method=method)
        with opener(request, timeout) as response:
            if not 200 <= response.status < 300:
                return f"{method} returned HTTP {response.status}"
            if read_limit is not None:
                response.read(read_limit)
            return None
    except HTTPError as exc:
        return f"{method} returned HTTP {exc.code}"
    except TimeoutError as exc:
        return f"{method} timed out: {exc}"
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError | socket.timeout):
            return f"{method} timed out: {reason}"
        return f"{method} failed: {reason}"
    except (HTTPException, ValueError) as exc:
        return f"{method} failed: {exc}"
    except OSError as exc:
        return f"{method} failed: {exc}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate an ECOS registry JSON file.")
    parser.add_argument(
        "registry",
        type=Path,
        nargs="?",
        default=Path("tool-registry.json"),
        help="Path to the registry JSON file.",
    )
    parser.add_argument(
        "--check-urls",
        action="store_true",
        help="Check lightweight reachability of each platform asset URL.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    issues = validate_registry(args.registry, check_urls=args.check_urls)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
