#!/usr/bin/env bash
# Sets up the Core Emulation Engine on this Mac: a private Python virtualenv in
# .venv holding the Quartz (pyobjc) bindings the engine needs. Safe to re-run —
# an existing .venv is reused and pip only installs what is missing.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "install.sh: this tool drives macOS input through Quartz and only runs on a Mac (this is $(uname -s))." >&2
  exit 1
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "install.sh: python3 was not found. Install the Xcode Command Line Tools (xcode-select --install) or Python from python.org, then re-run." >&2
  exit 1
fi

# WHY 3.9: the oldest Python the pinned pyobjc range ships wheels for, and the
# version Apple's Command Line Tools provide out of the box.
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "install.sh: $PYTHON is $("$PYTHON" --version 2>&1); Python 3.9 or newer is required." >&2
  exit 1
fi

if [[ -x .venv/bin/python ]]; then
  echo ".venv already exists — reusing it."
else
  echo "Creating .venv with $("$PYTHON" --version 2>&1) ..."
  "$PYTHON" -m venv .venv
fi

echo "Installing requirements into .venv ..."
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

if ! .venv/bin/python -c 'import Quartz.CoreGraphics' 2>/dev/null; then
  echo "install.sh: Quartz still cannot be imported from .venv. Delete .venv (rm -rf .venv) and re-run; check the pip output above." >&2
  exit 1
fi

cat <<'EOF'

Installed. One manual step remains — macOS must allow this tool to control input:

  1. Open  System Settings -> Privacy & Security -> Accessibility.
  2. Turn ON the terminal app you will run ./run.sh from (Terminal, iTerm, Warp, ...).
     If it is not listed, click "+" and add it (Terminal lives in
     /System/Applications/Utilities, iTerm and the rest in /Applications).
  3. If the engine starts but nothing moves or types, do the same under
     Privacy & Security -> Input Monitoring for the same app.
  4. Quit and reopen the terminal app after changing either toggle.

Then start it with:  ./run.sh
EOF
