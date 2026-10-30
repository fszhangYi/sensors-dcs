#!/usr/bin/env bash
# Convenience entry: desktop packaging.
#   Linux:  ./build.sh                 → Windows onedir via Wine
#           ./build.sh --target linux  → Linux onedir
#   Windows (Git Bash): same; prefer build.bat for native-win
#   ./build.sh --target windows
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/build-desktop.sh" "$@"
