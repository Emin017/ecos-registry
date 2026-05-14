#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _require_array(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"tool-registry.json {key} must be an array")
    return value


def _require_keys(entry: dict[str, Any], keys: tuple[str, ...], label: str) -> None:
    for key in keys:
        if key not in entry:
            raise ValueError(f"{label} entry is missing {key}")


def validate_registry(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("tool-registry.json must contain a JSON object")
    if data.get("schema_version") != 2:
        raise ValueError("tool-registry.json must use schema_version 2")

    tools = _require_array(data, "tools")
    pdks = _require_array(data, "pdks")

    for tool in tools:
        if not isinstance(tool, dict):
            raise ValueError("tool entry must be an object")
        _require_keys(
            tool,
            ("name", "display_name", "description", "category", "homepage", "versions"),
            "tool",
        )
        if not isinstance(tool["versions"], list):
            raise ValueError(f"tool {tool['name']} versions must be an array")

    for pdk in pdks:
        if not isinstance(pdk, dict):
            raise ValueError("pdk entry must be an object")
        _require_keys(pdk, ("id", "display_name", "versions"), "pdk")
        if not isinstance(pdk["versions"], list):
            raise ValueError(f"pdk {pdk['id']} versions must be an array")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate an ECOS registry JSON file.")
    parser.add_argument(
        "registry",
        type=Path,
        nargs="?",
        default=Path("tool-registry.json"),
        help="Path to the registry JSON file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        validate_registry(args.registry)
    except Exception as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()

