#!/bin/bash
set -euo pipefail

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
mkdir -p "$DEPS_DIR" "$LOCAL_PREFIX"

export PATH="$LOCAL_PREFIX/bin:$PATH"
export PKG_CONFIG_PATH="$LOCAL_PREFIX/share/pkgconfig:$LOCAL_PREFIX/lib/pkgconfig:$LOCAL_PREFIX/lib/x86_64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH:-}"

echo "=== Pinned dependency versions ==="
echo "  wayland:           $WAYLAND_VERSION"
echo "  wayland-protocols: $WAYLAND_PROTOCOLS_VERSION"
echo "  libdrm:            $LIBDRM_VERSION"
echo "  xkbcommon:         $XKBCOMMON_VERSION"
echo "  wlroots:           $WLROOTS_VERSION"
echo "  scenefx:           $SCENEFX_VERSION"
echo "  prefix:   $LOCAL_PREFIX"
echo ""

build_dep() {
  local name="$1" version="$2" url="$3" tag="$4"
  shift 4
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
  stamp="$LOCAL_PREFIX/.${name}-${version}-${commit}.stamp"
  if [ -f "$stamp" ]; then
    echo "  (stamp present: skipping rebuild)"
    return 0
  fi

  rm -rf build
  meson setup build \
    --prefix="$LOCAL_PREFIX" \
    --buildtype=release \
    -Ddefault_library=static \
    "${meson_args[@]}"
  ninja -C build
  ninja -C build install
  touch "$stamp"
}

build_dep wayland "$WAYLAND_VERSION" \
  "https://gitlab.freedesktop.org/wayland/wayland.git" "$WAYLAND_VERSION" \
  -Ddocumentation=false -Dtests=false

build_dep wayland-protocols "$WAYLAND_PROTOCOLS_VERSION" \
  "https://gitlab.freedesktop.org/wayland/wayland-protocols.git" "$WAYLAND_PROTOCOLS_VERSION"

build_dep libdrm "$LIBDRM_VERSION" \
  "https://gitlab.freedesktop.org/mesa/drm.git" "libdrm-$LIBDRM_VERSION" \
  -Dauto_features=disabled -Dtests=false

build_dep xkbcommon "$XKBCOMMON_VERSION" \
  "https://github.com/xkbcommon/libxkbcommon.git" "xkbcommon-$XKBCOMMON_VERSION" \
  -Denable-tools=false -Denable-x11=false -Denable-docs=false \
  -Denable-wayland=false -Denable-xkbregistry=false -Denable-bash-completion=false

build_dep wlroots "$WLROOTS_VERSION" \
  "https://gitlab.freedesktop.org/wlroots/wlroots.git" "$WLROOTS_VERSION" \
  -Dbackends=drm,libinput -Drenderers=gles2 -Dexamples=false -Dxwayland=enabled

build_dep scenefx "$SCENEFX_VERSION" \
  "https://github.com/wlrfx/scenefx.git" "0.5" \
  -Dexamples=false

echo ""
echo "=== Building mangowc ==="
cd "$SRC_DIR"

rm -rf build
meson setup build --prefix=/usr --buildtype=release
ninja -C build

ldd_output="$(ldd build/mango)"
printf '%s\n' "$ldd_output"
if printf '%s\n' "$ldd_output" | grep -qE 'not found|build/local'; then
  exit 1
fi

ls -lh build/mango build/mmsg
