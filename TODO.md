# KivotOS backlog

This file tracks follow-up work that is intentionally not part of the current implementation.

## P0 — Complete before calling the autonomous package flow production-ready

- [ ] Run an end-to-end package request through the issue form and verify the Ollama Cloud → package proposal → branch → PR flow.
- [ ] Add deterministic release preflight before accepting an AI proposal:
  - [ ] Resolve the exact release asset and version.
  - [ ] Detect `tar.gz`, `zip`, source archives, and Debian packages from the downloaded bytes.
  - [ ] Validate Debian architecture and ELF architecture.
  - [ ] Validate archive paths and reject path traversal.
  - [ ] Verify upstream checksum or signature when available.
  - [ ] Verify binary paths and install destinations.
- [ ] Prevent generated prebuilt packages from relying on AI-invented URLs, checksums, or dependencies.
- [ ] Add an end-to-end test for a package-only push that exercises the partial build matrix and full-set artifact assembly.

## P1 — Reliability and maintainability

- [ ] Add unit tests for changed-package detection:
  - [ ] `packages.lock` version changes.
  - [ ] Package-scoped file changes.
  - [ ] Shared workflow/script/config changes and full-build fallback.
  - [ ] Unknown packages and malformed lock entries.
  - [ ] Reverse runtime dependency closure.
- [ ] Add deterministic package strategy adapters for:
  - [ ] Release binary in `tar.gz`.
  - [ ] Release binary in `zip`.
  - [ ] Upstream `.deb`.
  - [ ] Cargo source.
  - [ ] Make/CMake/Meson source.
- [ ] Add a bounded CI repair loop for the package agent:
  - [ ] Maximum two repair attempts.
  - [ ] Collect and sanitize failed logs.
  - [ ] Update the PR or mark the request `needs-human`.
- [ ] Add risk policy for privileged packages, systemd units, Qt/Wayland/wlroots, and maintainer scripts.
- [ ] Add a label bootstrap/check for package-agent states.
- [ ] Add a package-agent dry-run mode that produces a proposal without pushing or opening a PR.
- [ ] Add structured audit output for model, source URL, selected asset, strategy, and validation results.

## P2 — Scale and automation improvements

- [ ] Add a resolved package lock format containing source strategy, asset, format, architecture, and digest.
- [ ] Add release asset resolution for GitHub, Codeberg, and GitLab with explicit ambiguity handling.
- [ ] Add independent reviewer-model fallback only after the single-model flow has production telemetry.
- [ ] Add automatic merge policy for low-risk packages only after review and security gates are proven.
- [ ] Add persistent artifact checkpoints on private R2 if full builds become expensive.
- [ ] Add immutable repository generations and by-hash publication if repository consistency requires it.
- [ ] Revisit architecture support and remove hardcoded `amd64` only when another architecture is a product requirement.

## Existing AI components

- `scripts/ci-report.js` remains the CI failure collector and analyzer.
- `scripts/ai-instructions.md` remains the CI failure analysis policy.
- `scripts/package_agent.py` and `scripts/package-agent-instructions.md` implement the first autonomous package intake flow.
- `AI_API_KEY`, `AI_URL`, and `AI_MODEL` are configured outside the repository and are not tracked here.
