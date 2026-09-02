#!/usr/bin/env bash
# Build delta.zip against the previous desktop release.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

exec python3 "$ROOT/scripts/release_delta.py" "$@"
