#!/usr/bin/env bash
# Starts the Snippets Workbench local server on port 4300,
# waits until it answers, and opens the dashboard in the default browser.
#
#   ./run.sh              start and open http://127.0.0.1:4300/
#   ./run.sh --no-open    start without opening a browser
#   PORT=4301 ./run.sh    use another port
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

OPEN_BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --no-open) OPEN_BROWSER=0 ;;
    -h|--help) echo "usage: ./run.sh [--no-open]"; exit 0 ;;
    *) echo "run.sh: unknown option '$arg' (only --no-open is supported)" >&2; exit 2 ;;
  esac
done

PORT="${PORT:-4300}"
URL="http://127.0.0.1:${PORT}"
PROBE_TIMEOUT=1

port_in_use() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
  else
    python3 -c '
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
' "$PORT"
  fi
}

if port_in_use; then
  echo "run.sh: port $PORT is already in use — another workbench is listening." >&2
  exit 1
fi

PORT="$PORT" python3 server.py &
SERVER_PID=$!

stop_server() {
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap stop_server EXIT INT TERM

for _ in $(seq 1 30); do
  if curl -s -m "$PROBE_TIMEOUT" -o /dev/null "$URL/healthz" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

echo "Workbench is up: $URL/   (Ctrl-C stops it)"
if [[ "$OPEN_BROWSER" == "1" ]]; then
  if command -v open >/dev/null 2>&1 && [[ "$(uname -s)" == "Darwin" ]]; then
    open "$URL/" || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL/" >/dev/null 2>&1 || true
  fi
fi

wait "$SERVER_PID" || true

