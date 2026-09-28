#!/usr/bin/env bash
# Starts the Core Emulation Engine from .venv, waits until it answers, opens the
# console in the default browser and keeps the engine in the foreground so
# Ctrl-C stops it.
#
#   ./run.sh              start and open http://127.0.0.1:4320/
#   ./run.sh --no-open    start without opening a browser
#   PORT=4321 ./run.sh    use another port (the console follows automatically)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

OPEN_BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --no-open) OPEN_BROWSER=0 ;;
    -h|--help) echo "usage: ./run.sh [--no-open]"; exit 0 ;;
    # WHY exit 2: the conventional "bad usage" code, distinct from the 1 used for runtime failures below.
    *) echo "run.sh: unknown option '$arg' (only --no-open is supported)" >&2; exit 2 ;;
  esac
done

if [[ ! -x .venv/bin/python ]]; then
  echo "run.sh: .venv is missing — run ./install.sh first." >&2
  exit 1
fi

# WHY 4320: must match DEFAULT_PORT in engine/mac_engine.py (one above task-notif's 4310).
PORT="${PORT:-4320}"
URL="http://127.0.0.1:${PORT}"
# WHY -m 1 on every curl: the engine is on loopback and answers in microseconds, so
# one second is already a generous ceiling; a longer one would only stall the loop.
CURL_TIMEOUT=1

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "run.sh: port $PORT is already in use — another engine (or something else) is listening." >&2
  echo "run.sh: find it with:  lsof -nP -iTCP:$PORT -sTCP:LISTEN   then stop it, or start with  PORT=<other> ./run.sh" >&2
  exit 1
fi

# activate is not written for `set -u`, so relax it for that one line.
set +u
# shellcheck disable=SC1091
source .venv/bin/activate
set -u

PORT="$PORT" python engine/mac_engine.py &
ENGINE_PID=$!

# Sends SIGTERM; the engine handles it like Ctrl-C (stops the loop, releases the
# Command key, prints "Shutdown complete.") and `wait` lets that finish.
stop_engine() {
  if kill -0 "$ENGINE_PID" 2>/dev/null; then
    kill "$ENGINE_PID" 2>/dev/null || true
    wait "$ENGINE_PID" 2>/dev/null || true
  fi
}
trap stop_engine EXIT INT TERM

# WHY 50 x 0.2 s: ten seconds is far more than the engine needs to bind, so a
# miss here means it crashed (its traceback is printed above), not that it is slow.
for _ in $(seq 1 50); do
  if curl -s -m "$CURL_TIMEOUT" -o /dev/null "$URL/status"; then
    break
  fi
  if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    echo "run.sh: the engine exited before answering on $URL — see the error above." >&2
    exit 1
  fi
  sleep 0.2
done

if ! curl -s -m "$CURL_TIMEOUT" -o /dev/null "$URL/status"; then
  echo "run.sh: the engine did not answer on $URL/status within 10 s." >&2
  exit 1
fi

echo "Engine is up: $URL/   (Ctrl-C stops it)"
if [[ "$OPEN_BROWSER" == "1" ]]; then
  open "$URL/" || echo "run.sh: could not open a browser — open $URL/ yourself."
fi

wait "$ENGINE_PID" || true
