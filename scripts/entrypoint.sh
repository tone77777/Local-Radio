#!/usr/bin/env bash
set -euo pipefail

echo "Local Radio container started"
echo "TZ=${TZ:-UTC}"
echo "DATA_DIR=${DATA_DIR:-/data}"

mkdir -p "${DATA_DIR:-/data}"

# Keep the container running so Synology/Docker can manage it.
# Replace this later with your real long-running process or cron loop.
while true; do
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) heartbeat"
  sleep "${HEARTBEAT_SECONDS:-60}"
done
