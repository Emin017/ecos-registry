from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse


SCHEMA_VERSION = 2
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
MPC_REQUIRED_FIELDS = (
    "id",
    "display_name",
    "description",
    "category",
    "homepage",
    "versions",
)
VERSION_REQUIRED_FIELDS = ("version", "platforms")
ALLOWED_VERSION_FIELDS = frozenset(VERSION_REQUIRED_FIELDS + ("requires",))
MPC_ALLOWED_VERSION_FIELDS = frozenset(VERSION_REQUIRED_FIELDS)
PLATFORM_REQUIRED_FIELDS = ("url", "sha256", "size")
UPDATE_SOURCE_REQUIRED_FIELDS = ("type", "branch")
ALLOWED_UPDATE_SOURCE_FIELDS = frozenset(UPDATE_SOURCE_REQUIRED_FIELDS)
GITHUB_BRANCH_UPDATE_SOURCE_TYPE = "github_branch"
ALLOWED_PLATFORM_FIELDS = frozenset(
    PLATFORM_REQUIRED_FIELDS
    + (
        "metadata_url",
        "sha256_url",
        "strip_prefix",
        "update_source",
        "supplemental_assets",
        "post_install",
    )
)
SUPPLEMENTAL_ASSET_REQUIRED_FIELDS = ("path", "url", "sha256", "size")
ALLOWED_SUPPLEMENTAL_ASSET_FIELDS = frozenset(SUPPLEMENTAL_ASSET_REQUIRED_FIELDS)
ARCHIVE_SUFFIXES = (".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".txz", ".zip")
SIDECAR_URL_SUFFIXES = (".json", ".sha256", ".txt")
REMOTE_LOCK_SOURCE_FIELDS = ("metadata_url", "sha256_url")
IDENTIFIER_RE = re.compile(r"^[a-z0-9_-]+$")
RESOURCE_DEPENDENCY_RE = re.compile(r"^(?:tool|pdk):[a-z0-9_-]+$")
GITHUB_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUMERIC_VERSION_RE = re.compile(r"^\d+(?:\.\d+)+$")


@dataclass(frozen=True)
class CollectionSchema:
    key: str
    resource_type: str
    label: str
    id_field: str
    required_fields: tuple[str, ...]
    allowed_version_fields: frozenset[str]


COLLECTION_SCHEMAS = (
    CollectionSchema(
        "tools",
        "tool",
        "tool",
        "name",
        TOOL_REQUIRED_FIELDS,
        ALLOWED_VERSION_FIELDS,
    ),
    CollectionSchema(
        "pdks",
        "pdk",
        "PDK",
        "id",
        PDK_REQUIRED_FIELDS,
        ALLOWED_VERSION_FIELDS,
    ),
    CollectionSchema(
        "mpcs",
        "mpc",
        "MPC",
        "id",
        MPC_REQUIRED_FIELDS,
        MPC_ALLOWED_VERSION_FIELDS,
    ),
)
ALLOWED_TOP_LEVEL_KEYS = frozenset(
    ("schema_version", *(schema.key for schema in COLLECTION_SCHEMAS))
)


@dataclass(frozen=True)
class RegistryPlatform:
    path: str
    value: dict[str, Any]
    homepage: str | None


def iter_registry_platforms(data: dict[str, Any]) -> Iterator[RegistryPlatform]:
    for collection_schema in COLLECTION_SCHEMAS:
        entries = data.get(collection_schema.key)
        if not isinstance(entries, list):
            continue
        for entry_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            versions = entry.get("versions")
            if not isinstance(versions, list):
                continue
            for version_index, version in enumerate(versions):
                if not isinstance(version, dict):
                    continue
                platforms = version.get("platforms")
                if not isinstance(platforms, dict):
                    continue
                for platform_key, platform in platforms.items():
                    if not isinstance(platform, dict):
                        continue
                    path = (
                        f"{collection_schema.key}[{entry_index}].versions"
                        f"[{version_index}].platforms.{platform_key}"
                    )
                    homepage = entry.get("homepage")
                    yield RegistryPlatform(
                        path=path,
                        value=platform,
                        homepage=homepage if isinstance(homepage, str) else None,
                    )


def has_remote_lock_source(platform: dict[str, Any]) -> bool:
    return any(
        isinstance(platform.get(field), str) and bool(platform[field])
        for field in REMOTE_LOCK_SOURCE_FIELDS
    )


def github_repository_from_homepage(homepage: object) -> str | None:
    if not isinstance(homepage, str) or not homepage:
        return None
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in homepage
    ):
        return None

    try:
        parsed = urlparse(homepage)
        if parsed.port is not None:
            return None
    except ValueError:
        return None

    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 2:
        return None
    repository = "/".join(path_parts)
    if GITHUB_REPOSITORY_RE.fullmatch(repository) is None:
        return None
    if parsed.path not in (f"/{repository}", f"/{repository}/"):
        return None
    return repository
