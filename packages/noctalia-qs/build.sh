#!/bin/bash
set -euo pipefail

stage_dir="$(pwd)/package-root"
protocol_dir="/usr/share/wayland-protocols/staging/ext-background-effect"
rm -rf build "$stage_dir"
install -Dm644 ../packages/noctalia-qs/protocols/ext-background-effect-v1.xml \
  "$protocol_dir/ext-background-effect-v1.xml"
cmake -GNinja -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr \
  -DDISTRIBUTOR=KivotOS \
  -DCRASH_HANDLER=OFF
cmake --build build
DESTDIR="$stage_dir" cmake --install build
install -Dm755 "$stage_dir/usr/bin/quickshell" "$stage_dir/usr/bin/qs"
