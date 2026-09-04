#!/usr/bin/env bash
# Start sensors-dcs viz on port 6006.
# Usage:
#   ./start-6006.sh                         # default: configs/default.yaml, dry_run from YAML
#   ./start-6006.sh configs/full_cell_plus.yaml
#   SENSORS_DCS_DRY_RUN=0 ./start-6006.sh   # force real hardware
#   SENSORS_DCS_AUTH_DISABLED=1 ./start-6006.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

export PYTHONUNBUFFERED=1
export SENSORS_DCS_PORT="${SENSORS_DCS_PORT:-6006}"
export SENSORS_DCS_VIZ_HOST="${SENSORS_DCS_VIZ_HOST:-127.0.0.1}"

CFG="${1:-configs/default.yaml}"
if [[ ! -f "$CFG" ]]; then
  echo "[start] config not found: $CFG" >&2
  exit 1
fi

echo "[start] config=$CFG"
echo "[start] open http://${SENSORS_DCS_VIZ_HOST}:${SENSORS_DCS_PORT}/login"
exec python3 -m sensors_dcs.desktop_main -c "$CFG"
