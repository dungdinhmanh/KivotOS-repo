#!/bin/bash
set -euo pipefail

: "${APP_VERSION:?}"

stage_dir="$(pwd)/package-root/usr/share/quickshell/noctalia-legacy"
archive="/tmp/noctalia-${APP_VERSION}.tar.gz"
mkdir -p "$stage_dir"
curl -fsSL "https://github.com/noctalia-dev/noctalia/releases/download/${APP_VERSION}/noctalia-${APP_VERSION}.tar.gz" -o "$archive"
tar -xzf "$archive" --strip-components=1 -C "$stage_dir"
rm -f "$archive"
