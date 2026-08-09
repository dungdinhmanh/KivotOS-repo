#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib import request

import tomllib

ROOT = Path(__file__).resolve().parent.parent
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
REPOSITORY_URL = re.compile(r"https?://(?:www\.)?(?:github\.com|codeberg\.org|gitlab\.com)/([^/]+/[^/#?]+)")


def run(command: list[str], check: bool = True) -> str:
    result = subprocess.run(command, check=check, capture_output=True, text=True)
    if check:
        return result.stdout.strip()
    return result.stdout.strip() or result.stderr.strip()


def gh(repo: str, *args: str, check: bool = True) -> str:
    return run(["gh", *args, "--repo", repo], check=check)


def comment(repo: str, issue: int, body: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file:
        file.write(body)
        path = file.name
    try:
        gh(repo, "issue", "comment", str(issue), "--body-file", path)
    finally:
        Path(path).unlink(missing_ok=True)


def label(repo: str, issue: int, name: str) -> None:
    run(["gh", "label", "create", name, "--repo", repo, "--force"], check=False)
    gh(repo, "issue", "edit", str(issue), "--add-label", name, check=False)


def issue_data(repo: str, issue: int) -> dict:
    return json.loads(
        gh(repo, "issue", "view", str(issue), "--json", "number,title,body,author,url,labels")
    )


def upstream_path(url: str) -> str | None:
    match = REPOSITORY_URL.search(url or "")
    if not match:
        return None
    return match.group(1).rstrip("/").removesuffix(".git")


def collect_upstream(repo_path: str) -> dict:
    owner, name = repo_path.split("/", 1)
    upstream = {
        "repository": json.loads(
            run(["gh", "repo", "view", repo_path, "--json", "nameWithOwner,defaultBranchRef,url,licenseInfo"])
        ),
        "files": [],
        "release": None,
    }
    try:
        entries = json.loads(
            run(["gh", "api", f"repos/{owner}/{name}/contents", "--jq", ".[] | .name"])
        )
        upstream["files"] = entries if isinstance(entries, list) else []
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        upstream["files"] = run(["gh", "api", f"repos/{owner}/{name}/contents", "--jq", ".[].name"], check=False).splitlines()
    release = run(
        ["gh", "release", "view", "--repo", repo_path, "--json", "tagName,publishedAt,assets"],
        check=False,
    )
    if release.startswith("{"):
        try:
            upstream["release"] = json.loads(release)
        except json.JSONDecodeError:
            pass
    return upstream


def load_instructions() -> str:
    return (ROOT / "scripts" / "package-agent-instructions.md").read_text(encoding="utf-8")


def call_ai(instructions: str, context: dict) -> dict:
    api_key = os.environ.get("AI_API_KEY") or os.environ.get("OLLAMA_API_KEY")
    if not api_key:
        raise RuntimeError("AI_API_KEY is not configured")
    url = os.environ.get("AI_URL")
    model = os.environ.get("AI_MODEL")
    if not url or not model:
        raise RuntimeError("AI_URL and AI_MODEL must be configured")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1},
    }
    http_request = request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=120) as response:
        data = json.loads(response.read())
    content = data.get("message", {}).get("content") or data.get("choices", [{}])[0].get("message", {}).get("content")
    if not content:
        raise RuntimeError("AI response did not contain message content")
    return json.loads(content)


def validate_proposal(proposal: dict) -> dict[str, str]:
    if proposal.get("decision") != "ready":
        raise ValueError(proposal.get("reason") or "AI marked package request as blocked")
    package_name = proposal.get("package_name", "")
    if not SAFE_NAME.fullmatch(package_name):
        raise ValueError("AI returned an invalid Debian package name")
    files = proposal.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("AI returned no package files")
    allowed_root = f"packages/{package_name}/"
    validated = {}
    for path, content in files.items():
        if not isinstance(path, str) or not path.startswith(allowed_root) or ".." in Path(path).parts:
            raise ValueError(f"AI returned an unsafe file path: {path}")
        if not isinstance(content, str) or "\x00" in content:
            raise ValueError(f"AI returned invalid file content: {path}")
        validated[path] = content
    manifest = validated.get(f"packages/{package_name}/package.toml")
    if not manifest:
        raise ValueError("Package proposal has no package.toml")
    parsed = tomllib.loads(manifest)
    if parsed.get("name") != package_name:
        raise ValueError("Manifest name does not match package directory")
    script = validated.get(f"packages/{package_name}/build.sh")
    if script:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sh") as file:
            file.write(script)
            file.flush()
            subprocess.run(["bash", "-n", file.name], check=True, capture_output=True, text=True)
    return validated


def blocked(repo: str, issue: int, reason: str) -> None:
    label(repo, issue, "needs-human")
    comment(repo, issue, f"## Package agent blocked\n\n{reason}\n\nNo files were committed and no pull request was opened.")


def ready(repo: str, issue: int, data: dict, files: dict[str, str]) -> None:
    package_name = data["package_name"]
    branch = f"bot/package-{issue}-{package_name}"
    run(["git", "switch", "-c", branch])
    for path, content in files.items():
        target = ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    run(["git", "add", *files])
    run(["git", "commit", "-m", f"Add {package_name} package"])
    run(["git", "push", "--set-upstream", "origin", branch])
    body = (
        f"Closes #{issue}\n\n"
        "This pull request was generated by the autonomous package agent.\n\n"
        f"Decision: `{data.get('decision')}`\n"
        f"Reason: {data.get('reason', 'validated package proposal')}\n\n"
        "Build and Debian smoke-test validation are required before merge."
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file:
        file.write(body)
        body_path = file.name
    try:
        pr = gh(repo, "pr", "create", "--base", "main", "--head", branch, "--title", f"Add {package_name} package", "--body-file", body_path)
    finally:
        Path(body_path).unlink(missing_ok=True)
    label(repo, issue, "build-pending")
    comment(repo, issue, f"## Package agent created a pull request\n\n{pr}\n\nCI validation is now required before merge.")
    run(["gh", "workflow", "run", "Build Packages", "--repo", repo, "--ref", branch, "-f", "publish=false"], check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    args = parser.parse_args()
    if not args.repo:
        raise SystemExit("GITHUB_REPOSITORY is required")
    data = issue_data(args.repo, args.issue)
    label(args.repo, args.issue, "ai-analyzing")
    try:
        upstream_url = next(
            (url for url in re.findall(r"https?://[^\s)]+", f"{data['title']}\n{data.get('body', '')}") if upstream_path(url)),
            None,
        )
        repo_path = upstream_path(upstream_url or "")
        if not repo_path:
            raise ValueError("No supported upstream repository URL was found in the issue")
        context = {"issue": data, "upstream": collect_upstream(repo_path), "target": {"distribution": "trixie", "architecture": "amd64"}}
        proposal = call_ai(load_instructions(), context)
        files = validate_proposal(proposal)
        ready(args.repo, args.issue, proposal, files)
        return 0
    except Exception as error:
        blocked(args.repo, args.issue, str(error))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
