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
  --target windows|linux|native|native-win
      windows     Linux host = Wine cross-build (default); Windows host = native PyInstaller
      native-win  Windows host only (same as windows on Win)
      linux|native  Linux host PyInstaller onedir
  --skip-frontend                 Skip frontend build (default; UI is embedded)
  --no-delta                      Skip incremental delta.zip vs previous release
  -h, --help                      Show this help

Environment:
  PIP_INDEX_URL           pip mirror (default: Tsinghua)
  WINEPREFIX              Wine prefix (default: .wine-sensors-dcs)
  WINEARCH                win64
  SENSORS_DCS_BUILD_TMP   short work/dist path
                          (Linux default: /root/autodl-tmp/tmp;
                           Windows native: <repo>/.tools/build-tmp)

Examples:
  ./build.sh
  ./build.sh --target windows
  ./build.sh --target linux
  bash scripts/build-desktop.sh --target windows
  # On Windows (cmd): build.bat   or   scripts\\build-desktop.bat
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

if [[ "$TARGET" != "windows" && "$TARGET" != "linux" && "$TARGET" != "native" && "$TARGET" != "native-win" ]]; then
  echo "error: --target must be windows, linux, native, or native-win" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found" >&2
  exit 1
fi

# Wine only for Linux→Windows cross-build. native-win / Windows host skip Wine.
if [[ "$TARGET" == "windows" ]]; then
  case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
      ;;
    *)
      if ! command -v wine64 >/dev/null 2>&1 && ! command -v wine >/dev/null 2>&1; then
        echo "error: wine64/wine not found — install Wine (win64) first" >&2
        echo "  (On Windows use build.bat / --target native-win instead)" >&2
        exit 1
      fi
      ;;
  esac
fi

if [[ "$TARGET" == "native-win" ]]; then
  case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
      ;;
    *)
      # Still allow if python reports win32 (unlikely under bash on Linux).
      if ! python3 -c "import sys; raise SystemExit(0 if sys.platform.startswith('win') else 1)"; then
        echo "error: --target native-win requires a Windows host" >&2
        exit 1
      fi
      ;;
  esac
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
