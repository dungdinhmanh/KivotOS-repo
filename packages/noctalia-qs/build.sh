#!/bin/bash
set -euo pipefail

stage_dir="$(pwd)/package-root"
rm -rf build "$stage_dir"
cmake -GNinja -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr \
  -DDISTRIBUTOR=KivotOS \
  -DCRASH_HANDLER=OFF
cmake --build build
DESTDIR="$stage_dir" cmake --install build
