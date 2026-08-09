#!/bin/bash
set -euo pipefail

PIXMAN_VERSION="0.46.0"
WAYLAND_VERSION="1.24.0"
WAYLAND_PROTOCOLS_VERSION="1.47"
LIBDRM_VERSION="2.4.129"
XKBCOMMON_VERSION="1.8.0"
WLROOTS_VERSION="0.20.2"
SCENEFX_VERSION="0.5.0"

SRC_DIR="$(pwd)"
ROOT_DIR="$(cd .. && pwd)"
DEPS_DIR="$ROOT_DIR/build/deps"
LOCAL_PREFIX="$ROOT_DIR/build/local"
STAGE_DIR="$SRC_DIR/package-root"
mkdir -p "$DEPS_DIR" "$LOCAL_PREFIX"

export PATH="$LOCAL_PREFIX/bin:$PATH"
export PKG_CONFIG_PATH="$LOCAL_PREFIX/share/pkgconfig:$LOCAL_PREFIX/lib/pkgconfig:$LOCAL_PREFIX/lib/x86_64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH:-}"

echo "=== Pinned dependency versions ==="
echo "  pixman:            $PIXMAN_VERSION"
echo "  wayland:           $WAYLAND_VERSION"
echo "  wayland-protocols: $WAYLAND_PROTOCOLS_VERSION"
echo "  libdrm:            $LIBDRM_VERSION"
echo "  xkbcommon:         $XKBCOMMON_VERSION"
echo "  wlroots:           $WLROOTS_VERSION"
echo "  scenefx:           $SCENEFX_VERSION"
echo "  prefix:   $LOCAL_PREFIX"
echo ""

build_dep() {
  local name="$1" version="$2" url="$3" tag="$4" library_type="$5"
  shift 5
  local meson_args=("$@")

  echo ""
  echo "=== $name $version ==="
  cd "$DEPS_DIR"

  if [ ! -d "$name/.git" ]; then
    rm -rf "$name"
    git clone --filter=blob:none --no-checkout "$url" "$name"
  fi
  cd "$name"
  git remote set-url origin "$url"
  git fetch --depth=1 origin "refs/tags/$tag"
  git checkout --detach --force FETCH_HEAD
  git clean -ffdx

  local commit stamp
  commit="$(git rev-parse HEAD)"
  stamp="$LOCAL_PREFIX/.${name}-${version}-${commit}-${library_type}.stamp"
  if [ -f "$stamp" ]; then
    echo "  (stamp present: skipping rebuild)"
    return 0
  fi

  rm -rf build
  meson setup build \
    --prefix="$LOCAL_PREFIX" \
    --buildtype=release \
    -Ddefault_library="$library_type" \
    "${meson_args[@]}"
  ninja -C build
  ninja -C build install
  touch "$stamp"
}

build_dep pixman "$PIXMAN_VERSION" \
  "https://gitlab.freedesktop.org/pixman/pixman.git" "pixman-$PIXMAN_VERSION" static

build_dep wayland "$WAYLAND_VERSION" \
  "https://gitlab.freedesktop.org/wayland/wayland.git" "$WAYLAND_VERSION" static \
  -Ddocumentation=false -Dtests=false

build_dep wayland-protocols "$WAYLAND_PROTOCOLS_VERSION" \
  "https://gitlab.freedesktop.org/wayland/wayland-protocols.git" "$WAYLAND_PROTOCOLS_VERSION" static

build_dep libdrm "$LIBDRM_VERSION" \
  "https://gitlab.freedesktop.org/mesa/drm.git" "libdrm-$LIBDRM_VERSION" static \
  -Dauto_features=disabled -Dtests=false

build_dep xkbcommon "$XKBCOMMON_VERSION" \
  "https://github.com/xkbcommon/libxkbcommon.git" "xkbcommon-$XKBCOMMON_VERSION" static \
  -Denable-tools=false -Denable-x11=false -Denable-docs=false \
  -Denable-wayland=false -Denable-xkbregistry=false -Denable-bash-completion=false

build_dep wlroots "$WLROOTS_VERSION" \
  "https://gitlab.freedesktop.org/wlroots/wlroots.git" "$WLROOTS_VERSION" shared \
  -Dbackends=drm,libinput -Drenderers=gles2 -Dexamples=false -Dxwayland=enabled

build_dep scenefx "$SCENEFX_VERSION" \
  "https://github.com/wlrfx/scenefx.git" "0.5" shared \
  -Dexamples=false

echo ""
echo "=== Building mangowc ==="
cd "$SRC_DIR"

rm -rf build "$STAGE_DIR"
meson setup build --prefix=/usr --buildtype=release
ninja -C build
DESTDIR="$STAGE_DIR" meson install -C build
install -d "$STAGE_DIR/usr/lib/x86_64-linux-gnu"
install -m755 \
  "$LOCAL_PREFIX/lib/x86_64-linux-gnu/libwlroots-0.20.so" \
  "$LOCAL_PREFIX/lib/x86_64-linux-gnu/libscenefx-0.5.so" \
  "$STAGE_DIR/usr/lib/x86_64-linux-gnu/"

mango_binary="$STAGE_DIR/usr/bin/mango"
ldd_output="$(LD_LIBRARY_PATH="$LOCAL_PREFIX/lib/x86_64-linux-gnu" ldd "$mango_binary")"
printf '%s\n' "$ldd_output"
if printf '%s\n' "$ldd_output" | grep -q 'not found'; then
  exit 1
fi
if readelf -d "$mango_binary" | grep -Fq "$ROOT_DIR"; then
  exit 1
fi

ls -lh \
  "$mango_binary" \
  "$STAGE_DIR/usr/bin/mmsg" \
  "$STAGE_DIR/usr/lib/x86_64-linux-gnu/libwlroots-0.20.so" \
  "$STAGE_DIR/usr/lib/x86_64-linux-gnu/libscenefx-0.5.so"
