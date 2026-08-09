#!/usr/bin/env python3

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib import request
from urllib.parse import urljoin

import tomllib

ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = ROOT / "packages"
CONFIG_FILE = ROOT / "repository.toml"
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")


@dataclass(frozen=True)
class PackageSpec:
    name: str
    binary: str
    architecture: str


@dataclass(frozen=True)
class DebArtifact:
    path: Path
    package: str
    version: str
    architecture: str
    sha256: str


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


def load_repository() -> dict:
    with CONFIG_FILE.open("rb") as stream:
        config = tomllib.load(stream)["repository"]
    required = {"name", "public_url", "distribution", "component", "architectures"}
    missing = sorted(required - config.keys())
    if missing:
        raise ValueError(f"Missing repository settings: {', '.join(missing)}")
    if len(config["architectures"]) != 1:
        raise ValueError("Artifact verification currently requires one repository architecture")
    return config


def load_package_specs() -> dict[str, PackageSpec]:
    specs = {}
    for path in sorted(PACKAGES_DIR.glob("*/package.toml")):
        with path.open("rb") as stream:
            manifest = tomllib.load(stream)
        name = manifest.get("name")
        binary = manifest.get("binary")
        architecture = manifest.get("distro", {}).get("arch")
        if path.parent.name != name:
            raise ValueError(f"{path}: directory and package name differ")
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
            raise ValueError(f"{path}: invalid package name")
        if not isinstance(binary, str) or not SAFE_NAME.fullmatch(binary):
            raise ValueError(f"{path}: invalid binary name")
        if not isinstance(architecture, str) or not SAFE_NAME.fullmatch(architecture):
            raise ValueError(f"{path}: invalid architecture")
        if name in specs:
            raise ValueError(f"Duplicate package manifest: {name}")
        specs[name] = PackageSpec(name, binary, architecture)
    if not specs:
        raise ValueError("No package manifests found")
    return specs


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_deb(path: Path) -> DebArtifact:
    result = run(["dpkg-deb", "--field", str(path)], capture_output=True)
    fields = parse_control(result.stdout)[0]
    missing = [field for field in ("Package", "Version", "Architecture") if not fields.get(field)]
    if missing:
        raise ValueError(f"{path}: missing control fields {', '.join(missing)}")
    return DebArtifact(
        path=path,
        package=fields["Package"],
        version=fields["Version"],
        architecture=fields["Architecture"],
        sha256=sha256(path),
    )


def inspect_artifacts(directory: Path, specs: dict[str, PackageSpec]) -> dict[str, DebArtifact]:
    artifacts = {}
    paths = sorted(directory.rglob("*.deb"))
    if not paths:
        raise ValueError(f"No .deb artifacts found in {directory}")
    for path in paths:
        artifact = inspect_deb(path)
        if artifact.package not in specs:
            raise ValueError(f"Unexpected package artifact: {artifact.package}")
        if artifact.package in artifacts:
            raise ValueError(f"Duplicate package artifact: {artifact.package}")
        expected_arch = specs[artifact.package].architecture
        if artifact.architecture != expected_arch:
            raise ValueError(
                f"{artifact.package}: architecture {artifact.architecture}, expected {expected_arch}"
            )
        artifacts[artifact.package] = artifact
    missing = sorted(set(specs) - set(artifacts))
    if missing:
        raise ValueError(f"Missing package artifacts: {', '.join(missing)}")
    return artifacts


def parse_control(text: str) -> list[dict[str, str]]:
    paragraphs = []
    current = {}
    key = None
    for line in text.splitlines() + [""]:
        if not line:
            if current:
                paragraphs.append(current)
                current = {}
                key = None
            continue
        if line[0].isspace() and key:
            current[key] += "\n" + line
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"Invalid Debian control line: {line}")
        key = key.strip()
        current[key] = value.strip()
    return paragraphs


def fetch(url: str, destination: Path) -> None:
    http_request = request.Request(url, headers={"User-Agent": "KivotOS-artifact-verifier"})
    with request.urlopen(http_request, timeout=30) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def verify_index(config: dict, workdir: Path) -> Path:
    base_url = config["public_url"].rstrip("/") + "/"
    distribution = config["distribution"]
    component = config["component"]
    architecture = config["architectures"][0]
    relative_path = f"{component}/binary-{architecture}/Packages"
    inrelease = workdir / "InRelease"
    packages = workdir / "Packages"
    public_key = workdir / "pubkey.gpg"
    fetch(urljoin(base_url, f"dists/{distribution}/InRelease"), inrelease)
    fetch(urljoin(base_url, "pubkey.gpg"), public_key)
    fetch(urljoin(base_url, f"dists/{distribution}/{relative_path}"), packages)

    gnupg_home = workdir / "gnupg"
    gnupg_home.mkdir(mode=0o700)
    env = {**os.environ, "GNUPGHOME": str(gnupg_home)}
    run(["gpg", "--batch", "--import", str(public_key)], env=env, capture_output=True)
    run(["gpg", "--batch", "--verify", str(inrelease)], env=env, capture_output=True)
    release_text = run(
        ["gpg", "--batch", "--decrypt", str(inrelease)],
        env=env,
        capture_output=True,
    ).stdout

    expected_hash = None
    in_sha256 = False
    for line in release_text.splitlines():
        if line == "SHA256:":
            in_sha256 = True
            continue
        if in_sha256 and line and not line[0].isspace():
            break
        if in_sha256:
            parts = line.split()
            if len(parts) == 3 and parts[2] == relative_path:
                expected_hash = parts[0]
                break
    if expected_hash is None:
        raise ValueError(f"InRelease has no SHA256 for {relative_path}")
    if sha256(packages) != expected_hash:
        raise ValueError("Production Packages index does not match signed InRelease")
    return packages


def latest_production_packages(path: Path) -> dict[str, dict[str, str]]:
    packages = {}
    for entry in parse_control(path.read_text()):
        name = entry.get("Package")
        version = entry.get("Version")
        if not name or not version:
            continue
        current = packages.get(name)
        if current is None or compare_versions(version, "gt", current["Version"]):
            packages[name] = entry
    return packages


def compare_versions(left: str, relation: str, right: str) -> bool:
    result = subprocess.run(
        ["dpkg", "--compare-versions", left, relation, right],
        check=False,
    )
    return result.returncode == 0


def copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if sha256(destination) != expected_sha256:
        destination.unlink(missing_ok=True)
        raise ValueError(f"Checksum mismatch: {source}")


def assemble_publish_set(
    candidates: dict[str, DebArtifact],
    production: dict[str, dict[str, str]],
    specs: dict[str, PackageSpec],
    config: dict,
    destination: Path,
    workdir: Path,
) -> dict[str, DebArtifact]:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    base_url = config["public_url"].rstrip("/") + "/"

    for name, candidate in sorted(candidates.items()):
        current = production.get(name)
        target = destination / candidate.path.name
        if current is None or compare_versions(candidate.version, "gt", current["Version"]):
            copy_verified(candidate.path, target, candidate.sha256)
            continue
        if compare_versions(candidate.version, "lt", current["Version"]):
            raise ValueError(
                f"{name}: candidate {candidate.version} is older than production {current['Version']}"
            )
        published_hash = current.get("SHA256")
        filename = current.get("Filename")
        if not published_hash or not filename:
            raise ValueError(f"{name}: production index lacks Filename or SHA256")
        if candidate.sha256 == published_hash:
            copy_verified(candidate.path, target, candidate.sha256)
            continue
        downloaded = workdir / "production" / name / Path(filename).name
        downloaded.parent.mkdir(parents=True, exist_ok=True)
        fetch(urljoin(base_url, filename), downloaded)
        copy_verified(downloaded, destination / downloaded.name, published_hash)
        print(f"{name}: preserved production artifact for unchanged version {candidate.version}")

    return inspect_artifacts(destination, specs)


def smoke_test(directory: Path, specs: dict[str, PackageSpec], config: dict) -> None:
    distribution = config["distribution"]
    image = f"debian:{distribution}-slim"
    binaries = " ".join(sorted(spec.binary for spec in specs.values()))
    script = """
set -eu
apt-get update
apt-get install -y /packages/*.deb
for binary in $BINARIES; do
    path=$(command -v "$binary")
    test -x "$path"
    if command -v ldd >/dev/null 2>&1; then
        output=$(ldd "$path" 2>&1 || true)
        if printf '%s\n' "$output" | grep -q 'not found'; then
            printf '%s\n' "$output" >&2
            exit 1
        fi
    fi
done
"""
    run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{directory.resolve()}:/packages:ro",
            "--env",
            f"BINARIES={binaries}",
            image,
            "sh",
            "-c",
            script,
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--publish-set", type=Path, required=True)
    parser.add_argument("--skip-smoke-test", action="store_true")
    args = parser.parse_args()

    try:
        config = load_repository()
        specs = load_package_specs()
        candidates = inspect_artifacts(args.artifacts, specs)
        expected_arch = config["architectures"][0]
        wrong_arch = sorted(name for name, spec in specs.items() if spec.architecture != expected_arch)
        if wrong_arch:
            raise ValueError(f"Manifest architecture differs from repository: {', '.join(wrong_arch)}")

        with tempfile.TemporaryDirectory() as temporary:
            workdir = Path(temporary)
            packages_index = verify_index(config, workdir)
            production = latest_production_packages(packages_index)
            publish_set = assemble_publish_set(
                candidates,
                production,
                specs,
                config,
                args.publish_set,
                workdir,
            )

        if not args.skip_smoke_test:
            smoke_test(args.publish_set, specs, config)

        for name, artifact in sorted(publish_set.items()):
            print(f"{name}\t{artifact.version}\t{artifact.architecture}\t{artifact.sha256}")

        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as output:
                output.write(f"repository={config['name']}\n")
                output.write(f"distribution={config['distribution']}\n")
                output.write(f"component={config['component']}\n")
                output.write(f"architecture={config['architectures'][0]}\n")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Artifact verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
