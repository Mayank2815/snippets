#!/usr/bin/env bash
# Starts the Core Emulation Engine from .venv, waits until it answers, opens the
# console in the default browser and keeps the engine in the foreground so
# Ctrl-C stops it. macOS and Linux; Windows uses run.ps1.
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

# WHY 4320: must match DEFAULT_PORT in engine/engine.py (one above task-notif's 4310).
PORT="${PORT:-4320}"
URL="http://127.0.0.1:${PORT}"
# WHY 1 s: the engine is on loopback and answers in microseconds, so one second
# is already a generous ceiling; a longer one would only stall the loop.
PROBE_TIMEOUT=1

# WHY these two fall back to .venv/bin/python: neither curl nor lsof is
# installed on a stock Ubuntu Desktop. Without a fallback, ./run.sh failed with
# "the engine did not answer" while the engine was up and healthy — the worst
# kind of error message, because it sends the user to debug the wrong thing.
# .venv/bin/python is the one interpreter we know exists: it was checked above.

engine_answers() {
  if command -v curl >/dev/null 2>&1; then
    curl -s -m "$PROBE_TIMEOUT" -o /dev/null "$URL/status"
  else
    .venv/bin/python -c '
import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=float(sys.argv[2])).read()
except Exception:
    sys.exit(1)
' "$URL/status" "$PROBE_TIMEOUT"
  fi
}

port_in_use() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
  else
    # Binding fails with EADDRINUSE while anything is listening there, which is
    # the same question lsof answers, without needing lsof.
    # WHY SO_REUSEADDR: http.server sets allow_reuse_address, so the engine can
    # bind a port still in TIME_WAIT from an engine that just exited. Without
    # the same option here the probe refused to start for a minute or so after
    # every stop, reporting "already in use" with nothing listening.
    ! .venv/bin/python -c '
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
  echo "run.sh: port $PORT is already in use — another engine (or something else) is listening." >&2
  if command -v lsof >/dev/null 2>&1; then
    echo "run.sh: find it with:  lsof -nP -iTCP:$PORT -sTCP:LISTEN   then stop it, or start with  PORT=<other> ./run.sh" >&2
  else
    echo "run.sh: find it with:  ss -ltnp 'sport = :$PORT'   then stop it, or start with  PORT=<other> ./run.sh" >&2
  fi
  exit 1
fi

# activate is not written for `set -u`, so relax it for that one line.
set +u
# shellcheck disable=SC1091
source .venv/bin/activate
set -u

PORT="$PORT" python engine/engine.py &
ENGINE_PID=$!

# Sends SIGTERM; the engine handles it like Ctrl-C (stops the loop, releases the
# switcher modifier, prints "Shutdown complete.") and `wait` lets that finish.
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
  if engine_answers; then
    break
  fi
  if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    echo "run.sh: the engine exited before answering on $URL — see the error above." >&2
    exit 1
  fi
  sleep 0.2
done

if ! engine_answers; then
  echo "run.sh: the engine did not answer on $URL/status within 10 s." >&2
  exit 1
fi

echo "Engine is up: $URL/   (Ctrl-C stops it)"
if [[ "$OPEN_BROWSER" == "1" ]]; then
  # `open` is macOS, `xdg-open` is Linux desktops; a headless Linux box has
  # neither, and then the URL printed above is all the user needs.
  if command -v open >/dev/null 2>&1 && [[ "$(uname -s)" == "Darwin" ]]; then
    open "$URL/" || echo "run.sh: could not open a browser — open $URL/ yourself."
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL/" >/dev/null 2>&1 || echo "run.sh: could not open a browser — open $URL/ yourself."
  fi
fi

wait "$ENGINE_PID" || true
