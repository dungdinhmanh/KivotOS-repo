#!/bin/bash
set -euo pipefail

: "${REPOSITORY:?}"
: "${SOURCE_REF:?}"

url="$REPOSITORY"
case "$url" in
  github:*) url="https://github.com/${url#github:}" ;;
  codeberg:*) url="https://codeberg.org/${url#codeberg:}" ;;
esac

rm -rf src
if [ "$SOURCE_REF" = "latest" ]; then
  git clone --depth=1 "$url" src
else
  git clone --filter=blob:none --no-checkout "$url" src
  git -C src fetch --depth=1 origin "$SOURCE_REF"
  git -C src checkout --detach FETCH_HEAD
fi

git -C src rev-parse HEAD
