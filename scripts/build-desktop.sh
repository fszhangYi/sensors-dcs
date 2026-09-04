#!/usr/bin/env bash
# Desktop packaging entry for sensors-dcs (Linux → Windows via Wine, or native Linux).
# Skills: scheme-a-linux-to-windows-desktop, scheme-a-linux-to-linux-desktop
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export WINEDEBUG="${WINEDEBUG:--all}"
export WINEARCH="${WINEARCH:-win64}"
export WINEPREFIX="${WINEPREFIX:-$ROOT/.wine-sensors-dcs}"
export SENSORS_DCS_BUILD_TMP="${SENSORS_DCS_BUILD_TMP:-/root/autodl-tmp/tmp}"

TARGET="windows"
SKIP_FRONTEND=1
EXTRA=()

usage() {
  cat <<'EOF'
Usage: bash scripts/build-desktop.sh [options]

Build a self-contained desktop onedir under release/.

Options:
  --target windows|linux|native   windows = Wine (default); linux/native = host PyInstaller
  --skip-frontend                 Skip frontend build (default; UI is embedded)
  --no-delta                      Skip incremental delta.zip vs previous release
  -h, --help                      Show this help

Environment:
  PIP_INDEX_URL           pip mirror (default: Tsinghua)
  WINEPREFIX              Wine prefix (default: .wine-sensors-dcs)
  WINEARCH                win64
  SENSORS_DCS_BUILD_TMP   short work/dist path (default: /root/autodl-tmp/tmp)

Examples:
  ./build.sh
  ./build.sh --target windows
  ./build.sh --target linux
  bash scripts/build-desktop.sh --target windows
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET="${2:-}"
      shift 2
      ;;
    --skip-frontend)
      SKIP_FRONTEND=1
      shift
      ;;
    --no-delta)
      EXTRA+=("--no-delta")
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      EXTRA+=("$1")
      shift
      ;;
  esac
done

if [[ "$TARGET" != "windows" && "$TARGET" != "linux" && "$TARGET" != "native" ]]; then
  echo "error: --target must be windows, linux, or native" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found" >&2
  exit 1
fi

if [[ "$TARGET" == "windows" ]]; then
  if ! command -v wine64 >/dev/null 2>&1 && ! command -v wine >/dev/null 2>&1; then
    echo "error: wine64/wine not found — install Wine (win64) first" >&2
    exit 1
  fi
fi

if [[ ! -d "$ROOT/sensors/src/sensors" ]]; then
  echo "error: missing ./sensors symlink to hik-sensors" >&2
  echo "  ln -sfn ~/autodl-tmp/sensors \"$ROOT/sensors\"" >&2
  exit 1
fi

ARGS=(--target "$TARGET")
if [[ "$SKIP_FRONTEND" -eq 1 ]]; then
  ARGS+=(--skip-frontend)
fi
if [[ ${#EXTRA[@]} -gt 0 ]]; then
  ARGS+=("${EXTRA[@]}")
fi

echo "==> sensors-dcs desktop build ($TARGET)"
echo "    root:      $ROOT"
echo "    target:    $TARGET"
echo "    pip index: $PIP_INDEX_URL"
echo "    wineprefix:$WINEPREFIX"
echo "    build tmp: $SENSORS_DCS_BUILD_TMP"
echo

exec python3 "$ROOT/scripts/build_desktop.py" "${ARGS[@]}"
