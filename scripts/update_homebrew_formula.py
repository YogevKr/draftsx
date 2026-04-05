#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from pathlib import Path


URL_TEMPLATE = '  url "https://github.com/YogevKr/draftsx/releases/download/v{version}/draftsx-{version}.tar.gz"'
SHA_TEMPLATE = '  sha256 "{sha256}"'


def update_formula_text(text: str, *, version: str, sha256: str) -> str:
    if "class Draftsx < Formula" not in text:
        raise ValueError("Not a draftsx Homebrew formula")

    updated, url_count = re.subn(r'^  url ".*"$', URL_TEMPLATE.format(version=version), text, count=1, flags=re.MULTILINE)
    if url_count != 1:
        raise ValueError("Could not update formula url line")

    updated, sha_count = re.subn(r'^  sha256 ".*"$', SHA_TEMPLATE.format(sha256=sha256), updated, count=1, flags=re.MULTILINE)
    if sha_count != 1:
        raise ValueError("Could not update formula sha256 line")

    return updated


def update_formula_file(path: Path, *, version: str, sha256: str) -> None:
    original = path.read_text()
    updated = update_formula_text(original, version=version, sha256=sha256)
    if updated != original:
        path.write_text(updated)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update the draftsx Homebrew formula for a new release.")
    parser.add_argument("--formula", required=True, type=Path, help="Path to Formula/draftsx.rb")
    parser.add_argument("--version", required=True, help="Release version without the leading v")
    parser.add_argument("--sha256", required=True, help="SHA256 for the source tarball")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    update_formula_file(args.formula, version=args.version, sha256=args.sha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
