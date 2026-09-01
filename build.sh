#!/usr/bin/env bash
# Convenience entry: Windows desktop packaging via Wine.
#   ./build.sh                 → Windows onedir zip
#   ./build.sh --target windows
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/build-desktop.sh" "$@"
