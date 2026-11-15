#!/usr/bin/env bash
# Starts both the Core Emulation Engine (:4320) and the Workbench Dashboard (:4300),
# then opens the unified dashboard in the default browser.
#
#   ./run.sh              start both and open http://127.0.0.1:4300/
#   ./run.sh --no-open    start both without opening a browser
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

OPEN_BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --no-open) OPEN_BROWSER=0 ;;
    -h|--help) echo "usage: ./run.sh [--no-open]"; exit 0 ;;
    *) echo "run.sh: unknown option '$arg'" >&2; exit 2 ;;
  esac
done

echo "Starting Core Emulation Engine on :4320..."
( cd tools/emulation-engine && ./run.sh --no-open ) &
ENGINE_JOB=$!

cleanup() {
  echo "\nStopping services..."
  if kill -0 "$ENGINE_JOB" 2>/dev/null; then
    kill "$ENGINE_JOB" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Wait for engine to respond
for _ in $(seq 1 30); do
  if curl -s -m 1 -o /dev/null "http://127.0.0.1:4320/status" 2>/dev/null; then
    break
  fi
  sleep 0.2
done

echo "Starting Workbench Dashboard on :4300..."
if [[ "$OPEN_BROWSER" == "1" ]]; then
  cd tools/workbench && ./run.sh
else
  cd tools/workbench && ./run.sh --no-open
fi

