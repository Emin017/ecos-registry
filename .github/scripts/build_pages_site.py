#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path


def build_site(output_dir: Path, files: list[Path]) -> None:
    output_dir.mkdir(exist_ok=True)
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    for source in files:
        target = output_dir / source.name
        if source.is_dir():
            copy_directory(source, target)
        else:
            target.write_bytes(source.read_bytes())


def copy_directory(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(source)
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(path.read_bytes())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the ECOS registry Pages site.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("_site"),
        help="Directory to write the Pages artifact into.",
    )
    parser.add_argument(
        "files",
        type=Path,
        nargs="+",
        help="Files to copy into the Pages artifact.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_site(args.output_dir, args.files)


if __name__ == "__main__":
    main()
