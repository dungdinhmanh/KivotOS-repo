#!/usr/bin/env python3
"""
KivotOS Package Manager.

Commands:
    matrix      - Emit GitHub Actions build matrix as JSON
    update      - Check upstream releases and refresh packages.lock
    nfpm-config - Generate nfpm.yaml for a single package (used by CI)
"""

import argparse
import concurrent.futures
import json
import os
import sys
from pathlib import Path

import toml
import yaml

LOCK_FILE       = Path("packages.lock")
PACKAGES_DIR    = Path("packages")
REPOSITORY_FILE = Path("repository.toml")
GITHUB_API   = "https://api.github.com"
CODEBERG_API = "https://codeberg.org/api/v1"
REQUEST_TIMEOUT = 10


def iter_package_tomls() -> list[Path]:
    """Return sorted list of packages/<name>/package.toml files."""
    return sorted(PACKAGES_DIR.glob("*/package.toml"))


def pkg_name_of(toml_path: Path) -> str:
    return toml_path.parent.name


def load_repository() -> dict:
    return toml.loads(REPOSITORY_FILE.read_text())["repository"]


def load_lock() -> dict[str, str]:
    lock = {}
    if LOCK_FILE.exists():
        for line in LOCK_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                lock[key.strip()] = val.strip()
    return lock


def save_lock(lock: dict[str, str]) -> None:
    lines = ["# This file is automatically managed by update.yml\n"]
    for key in sorted(lock):
        lines.append(f"{key}={lock[key]}\n")
    LOCK_FILE.write_text("".join(lines))


def get_latest_version(repo: str) -> str | None:
    """Resolve "github:owner/repo" or "codeberg:owner/repo" to its latest tag."""
    import requests

    if ":" not in repo:
        return None

    provider, path = repo.split(":", 1)

    try:
        if provider == "github":
            url = f"{GITHUB_API}/repos/{path}/releases/latest"
            headers = {}
            token = os.environ.get("GITHUB_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json().get("tag_name")

        elif provider == "codeberg":
            url = f"{CODEBERG_API}/repos/{path}/releases"
            r = requests.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            releases = r.json()
            if releases:
                return releases[0].get("tag_name")

    except requests.exceptions.Timeout:
        print(f"  ⏱ Timeout: {repo}", file=sys.stderr)
    except requests.exceptions.HTTPError as e:
        print(f"  ❌ HTTP {e.response.status_code}: {repo}", file=sys.stderr)
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Error fetching {repo}: {e}", file=sys.stderr)

    return None


def _check_one(args: tuple) -> tuple[str, str | None]:
    pkg_name, repo = args
    print(f"  checking {pkg_name} ({repo})...", flush=True)
    return pkg_name, get_latest_version(repo)



def generate_nfpm_yaml(pkg: dict, src_dir: str = "src") -> str:
    """
    Build an nfpm.yaml for non-cargo packages. Cargo packages skip this and
    use cargo-deb, which reads upstream Cargo.toml directly.
    """
    depends = pkg.get("depends", {})
    control = pkg.get("control", {})
    install = pkg.get("install", {})

    contents = []
    for src, dst in install.items():
        is_executable = any(dst.startswith(p) for p in [
            "/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/",
        ])
        contents.append({
            "src": f"{src_dir}/{src}",
            "dst": dst,
            "file_info": {
                "mode": 0o755 if is_executable else 0o644,
            },
        })

    config = {
        "name":        pkg["name"],
        "arch":        "amd64",
        "version":     pkg["version"],  # nfpm strips a leading 'v' itself
        "maintainer":  control.get("maintainer", "KivotOS <kivotos@example.com>"),
        "description": pkg.get("description", ""),
        "homepage":    control.get("homepage", ""),
        "license":     pkg.get("license", ""),
        "section":     control.get("section", "utils"),
        "priority":    control.get("priority", "optional"),
        "depends":     depends.get("runtime", []),
        "suggests":    depends.get("optional", []),
        "contents":    contents,
    }
    config = {k: v for k, v in config.items() if v or v == 0}

    return yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)


def set_output(name: str, value: str) -> None:
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"{name}={value}\n")
    else:
        print(f"[OUTPUT] {name}={value}")


def write_summary(lines: list[str]) -> None:
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("\n".join(lines) + "\n")


def cmd_update(args) -> None:
    lock = load_lock()

    all_toml = iter_package_tomls()
    if args.package:
        all_toml = [f for f in all_toml if pkg_name_of(f) == args.package]
        if not all_toml:
            print(f"❌ Package '{args.package}' not found", file=sys.stderr)
            sys.exit(1)

    to_check = []
    for toml_file in all_toml:
        try:
            data = toml.loads(toml_file.read_text())
        except toml.TomlDecodeError as e:
            print(f"⚠ Error parsing {toml_file}: {e}", file=sys.stderr)
            continue
        repo = data.get("repo")
        if repo and data.get("update", True):
            to_check.append((pkg_name_of(toml_file), repo))

    if not to_check:
        print("No packages to check.")
        set_output("updated", "false")
        return

    print(f"Checking {len(to_check)} package(s) in parallel...")

    updated = False
    summary_rows = ["## Package Updates\n", "| Package | Old | New |", "|---|---|---|"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for pkg_name, latest in executor.map(_check_one, to_check):
            if latest is None:
                print(f"  ⚠ {pkg_name}: could not fetch version")
                continue
            current = lock.get(pkg_name)
            if current != latest:
                print(f"  ✅ {pkg_name}: {current} → {latest}")
                summary_rows.append(f"| {pkg_name} | `{current}` | `{latest}` |")
                lock[pkg_name] = latest
                updated = True
            else:
                print(f"  — {pkg_name}: {current} (up-to-date)")

    if updated:
        save_lock(lock)
        write_summary(summary_rows)

    set_output("updated", "true" if updated else "false")


def cmd_matrix(args) -> None:
    lock = load_lock()
    repository = load_repository()
    build_image = f"debian:{repository['distribution']}-slim"

    matrix = []
    for toml_file in iter_package_tomls():
        pkg_name = pkg_name_of(toml_file)

        try:
            data = toml.loads(toml_file.read_text())
        except toml.TomlDecodeError as e:
            print(f"⚠ Error parsing {toml_file}: {e}", file=sys.stderr)
            continue

        missing = [f for f in ("name", "repo", "type") if not data.get(f)]
        if missing:
            print(f"⚠ {toml_file.name} missing required fields: {missing}", file=sys.stderr)
            continue

        matrix.append({
            "name":        pkg_name,
            "version":     lock.get(pkg_name, data.get("version", "latest")),
            "repo":        data["repo"],
            "type":        data["type"],
            "deb_package": data.get("deb_package", ""),
            "toolchain":   data.get("toolchain", ""),
            "build_cmd":   data.get("build", ""),
            "post_build":  data.get("post_build", ""),
            "install":     data.get("install", {}),
            "depends":     data.get("depends", {"build": [], "runtime": []}),
            "control":     data.get("control", {}),
            "description": data.get("description", ""),
            "license":     data.get("license", ""),
            "cache":       data.get("cache", None),
            "build_image": build_image,
        })

    if not matrix:
        print("No packages to build.", file=sys.stderr)

    print(json.dumps({"include": matrix}))


def cmd_nfpm_config(args) -> None:
    toml_file = PACKAGES_DIR / args.package / "package.toml"
    if not toml_file.exists():
        print(f"❌ {toml_file} not found", file=sys.stderr)
        sys.exit(1)

    lock = load_lock()
    data = toml.loads(toml_file.read_text())
    data["version"] = lock.get(args.package, data.get("version", "latest"))

    yaml_content = generate_nfpm_yaml(data, src_dir=args.src_dir)
    Path(args.output).write_text(yaml_content)
    print(f"✅ Generated {args.output}")


def main():
    parser = argparse.ArgumentParser(
        description="KivotOS Package Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/manager.py matrix
  python scripts/manager.py update
  python scripts/manager.py update --package yazi
  python scripts/manager.py nfpm-config --package hellwal --src-dir src --output /tmp/nfpm.yaml
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("matrix", help="Generate GitHub Actions build matrix")

    p_update = sub.add_parser("update", help="Check and update package versions")
    p_update.add_argument("--package", "-p", default=None,
                          help="Check only this package (default: all)")

    p_nfpm = sub.add_parser("nfpm-config", help="Generate nfpm.yaml for a package")
    p_nfpm.add_argument("--package", required=True, help="Package name")
    p_nfpm.add_argument("--src-dir", default="src",       help="Source directory (default: src)")
    p_nfpm.add_argument("--output",  default="nfpm.yaml", help="Output path (default: nfpm.yaml)")

    args = parser.parse_args()

    if args.command == "matrix":
        cmd_matrix(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "nfpm-config":
        cmd_nfpm_config(args)


if __name__ == "__main__":
    main()
