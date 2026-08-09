#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
from pathlib import Path

PACKAGE_PATH = re.compile(r"^packages/([^/]+)/")
LOCK_ENTRY = re.compile(r"^[+-](?![+-])([a-z0-9][a-z0-9+.-]*)\s*=")
SHARED_PATHS = {"packages.lock", "repository.toml"}


def changed_paths(base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..{head}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [path for path in result.stdout.splitlines() if path]


def changed_from_lock(base: str, head: str) -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--unified=0", f"{base}..{head}", "--", "packages.lock"],
        check=True,
        capture_output=True,
        text=True,
    )
    packages = set()
    for line in result.stdout.splitlines():
        match = LOCK_ENTRY.match(line)
        if match:
            packages.add(match.group(1))
    return packages


def package_names() -> set[str]:
    return {path.parent.name for path in Path("packages").glob("*/package.toml")}


def detect(base: str, head: str) -> dict:
    paths = changed_paths(base, head)
    if not paths:
        return {"mode": "full", "packages": [], "reason": "no changed paths"}

    selected = changed_from_lock(base, head)
    for path in paths:
        if path == "packages.lock":
            continue
        match = PACKAGE_PATH.match(path)
        if not match:
            return {"mode": "full", "packages": [], "reason": f"shared path changed: {path}"}
        selected.add(match.group(1))

    known = package_names()
    unknown = sorted(selected - known)
    if unknown:
        return {"mode": "full", "packages": [], "reason": f"unknown package: {', '.join(unknown)}"}
    return {"mode": "partial", "packages": sorted(selected), "reason": "package-scoped changes"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    print(json.dumps(detect(args.base, args.head), separators=(",", ":")))


if __name__ == "__main__":
    main()
