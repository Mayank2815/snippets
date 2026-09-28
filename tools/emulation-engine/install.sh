#!/usr/bin/env bash
# Sets up the Core Emulation Engine on this machine: a private Python virtualenv
# in .venv holding the input library the engine needs — Quartz (pyobjc) on
# macOS, pynput on Linux. Safe to re-run — an existing .venv is reused and pip
# only installs what is missing. Windows uses install.ps1 instead.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

OS="$(uname -s)"
if [[ "$OS" != "Darwin" && "$OS" != "Linux" ]]; then
  echo "install.sh: this script supports macOS and Linux (this is $OS). On Windows run install.ps1 in PowerShell." >&2
  exit 1
fi

PKG_MANAGER=""
if [[ "$OS" == "Linux" ]]; then
  # packages.sh knows which package manager this machine uses and what each
  # requirement is called there. It is pure bash and only defines functions, so
  # it can be sourced before we know whether python3 even exists.
  if [[ ! -r packages.sh ]]; then
    echo "install.sh: packages.sh is missing from this folder, and install.sh needs it to name the" >&2
    echo "install.sh: packages your distribution uses. Re-copy the whole emulation-engine folder." >&2
    exit 1
  fi
  # shellcheck source=packages.sh
  source ./packages.sh
  PKG_MANAGER="$(pkg_manager)"
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  if [[ "$OS" == "Darwin" ]]; then
    echo "install.sh: python3 was not found. Install the Xcode Command Line Tools (xcode-select --install) or Python from python.org, then re-run." >&2
  else
    python_line="$(pkg_install_command "$PKG_MANAGER" python venv || true)"
    if [[ "$PKG_MANAGER" == "apt" ]]; then
      echo "install.sh: python3 was not found. Install it with your package manager (Debian/Ubuntu:  $python_line), then re-run." >&2
    elif [[ -n "$python_line" ]]; then
      echo "install.sh: python3 was not found. Install it with:  $python_line   then re-run." >&2
    else
      echo "install.sh: python3 was not found. Install $(pkg_generic_description python) with your package manager, then re-run." >&2
    fi
  fi
  exit 1
fi

if [[ "$OS" == "Darwin" ]]; then
  # WHY check the Command Line Tools before running python: on a Mac without them,
  # `command -v python3` still finds Apple's /usr/bin/python3, which is only a stub
  # that prints "xcode-select: note: No developer tools were found..." and exits.
  # The version check below used to print that note as if it were a Python version.
  if ! xcode-select -p >/dev/null 2>&1 && ! "$PYTHON" -c 'pass' >/dev/null 2>&1; then
    echo "install.sh: the Xcode Command Line Tools are not installed, so '$PYTHON' is only Apple's placeholder." >&2
    echo "install.sh: install them with:  xcode-select --install   (or install Python from python.org), then re-run." >&2
    exit 1
  fi
fi
if ! "$PYTHON" -c 'pass' >/dev/null 2>&1; then
  echo "install.sh: '$PYTHON' cannot run at all:" >&2
  "$PYTHON" -c 'pass' >&2 || true
  exit 1
fi

# WHY 3.9: the oldest Python the pinned pyobjc range ships wheels for, and the
# version Apple's Command Line Tools provide out of the box; pynput has no
# tighter floor.
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "install.sh: $PYTHON is $("$PYTHON" --version 2>&1); Python 3.9 or newer is required." >&2
  exit 1
fi

# WHY a Linux preflight instead of discovering these one failure at a time: a
# stock Ubuntu Desktop is missing BOTH the venv module and a C toolchain, and
# without this the user hits two separate failures, two apt commands and three
# runs of this script. pynput's evdev dependency publishes an sdist and no
# wheels for any architecture, so the compiler is needed on every Linux machine,
# not just unusual ones — it is safe to insist on it up front.
if [[ "$OS" == "Linux" ]]; then
  # The needs are abstract on purpose — "the Python headers" is python3-dev on
  # Debian, python3-devel on Fedora and openSUSE, and part of plain `python` on
  # Arch. packages.sh turns each one into the right name for this machine.
  missing_needs=()
  missing_why=()

  if ! "$PYTHON" -c 'import ensurepip' >/dev/null 2>&1; then
    missing_needs+=("venv")
    missing_why+=("without it '$PYTHON -m venv' cannot create .venv")
  fi
  if ! command -v cc >/dev/null 2>&1 && ! command -v gcc >/dev/null 2>&1; then
    missing_needs+=("compiler")
    missing_why+=("a C compiler, to build pynput's evdev dependency")
  elif [[ ! -e /usr/include/linux/input.h ]]; then
    # linux/input.h comes from linux-libc-dev on Debian, kernel-headers on
    # Fedora, linux-api-headers on Arch — all normally pulled in by the
    # compiler package, so this branch only fires on an unusual machine.
    missing_needs+=("kernel_headers")
    missing_why+=("brings linux/input.h, which evdev needs to build")
  fi
  if ! "$PYTHON" -c 'import os, sysconfig, sys; sys.exit(0 if os.path.exists(os.path.join(sysconfig.get_paths()["include"], "Python.h")) else 1)' >/dev/null 2>&1; then
    missing_needs+=("python_headers")
    missing_why+=("Python.h, which evdev needs to build")
  fi

  if [[ ${#missing_needs[@]} -gt 0 ]]; then
    echo "install.sh: this machine is missing some system packages the install needs:" >&2
    for i in "${!missing_needs[@]}"; do
      need="${missing_needs[$i]}"
      need_pkg="$(pkg_name "$PKG_MANAGER" "$need")"
      if [[ -n "$need_pkg" ]]; then
        printf '    %-15s — %s\n' "$need_pkg" "${missing_why[$i]}" >&2
      else
        # No package name to give, so name the requirement itself.
        printf '    %s\n' "$(pkg_generic_description "$need")" >&2
      fi
    done
    echo >&2
    if install_line="$(pkg_install_command "$PKG_MANAGER" "${missing_needs[@]}")"; then
      echo "install.sh: install them all in one go with:" >&2
      echo >&2
      echo "    $install_line" >&2
      echo >&2
      echo "install.sh: then re-run ./install.sh." >&2
      exit 1
    fi
    # Unrecognised package manager: printing a package name that does not exist
    # on this distribution would be worse than printing none, so name the
    # requirements in plain words and carry on — pip may still succeed below.
    echo "install.sh: this machine's package manager was not recognised — none of apt, dnf, pacman" >&2
    echo "install.sh: or zypper is on PATH — so there is no exact command to print. Install the" >&2
    echo "install.sh: things listed above using whatever your distribution provides, then re-run." >&2
    echo >&2
  fi
fi

if [[ -x .venv/bin/python ]]; then
  echo ".venv already exists — reusing it."
else
  echo "Creating .venv with $("$PYTHON" --version 2>&1) ..."
  if ! "$PYTHON" -m venv .venv; then
    if [[ "$OS" == "Linux" ]]; then
      # WHY: Debian and Ubuntu ship python3 without the venv module; it is a
      # separate package, and `python3 -m venv` fails with "ensurepip is not
      # available" until it is installed. Fedora, Arch and openSUSE bundle it,
      # so there the fix is to install (or repair) python3 itself.
      venv_line="$(pkg_install_command "$PKG_MANAGER" venv || true)"
      if [[ "$PKG_MANAGER" == "apt" ]]; then
        echo "install.sh: could not create .venv. On Debian/Ubuntu install the venv module with:" >&2
        echo "    $venv_line" >&2
        echo "then re-run ./install.sh." >&2
      elif [[ -n "$venv_line" ]]; then
        echo "install.sh: could not create .venv. Install Python's venv support with:" >&2
        echo "    $venv_line" >&2
        echo "then re-run ./install.sh." >&2
      else
        echo "install.sh: could not create .venv. Install $(pkg_generic_description venv) with your" >&2
        echo "install.sh: distribution's package manager, then re-run ./install.sh." >&2
      fi
    else
      echo "install.sh: could not create .venv with $PYTHON — see the error above." >&2
    fi
    rm -rf .venv
    exit 1
  fi
fi

echo "Installing requirements into .venv ..."
.venv/bin/python -m pip install --quiet --upgrade pip
if ! .venv/bin/python -m pip install --quiet -r requirements.txt; then
  if [[ "$OS" == "Linux" ]]; then
    # WHY: pynput depends on evdev, which is a C extension with no binary
    # wheel; pip builds it and needs a compiler plus the Python and kernel
    # headers. The line below names the packages that provide all three on
    # whichever distribution this is.
    echo "install.sh: pip could not install the requirements. If the error above mentions evdev or a missing" >&2
    echo "install.sh: compiler/header (gcc, Python.h, linux/input.h), install the build tools with:" >&2
    build_line="$(pkg_install_command "$PKG_MANAGER" compiler python_headers || true)"
    if [[ -n "$build_line" ]]; then
      echo "    $build_line" >&2
      echo "then re-run ./install.sh." >&2
    else
      echo "    $(pkg_generic_description compiler) and $(pkg_generic_description python_headers)," >&2
      echo "    installed with whatever your distribution provides, then re-run ./install.sh." >&2
    fi
  else
    echo "install.sh: pip could not install the requirements — see the error above." >&2
  fi
  exit 1
fi

if [[ "$OS" == "Darwin" ]]; then
  if ! .venv/bin/python -c 'import Quartz.CoreGraphics' 2>/dev/null; then
    echo "install.sh: Quartz still cannot be imported from .venv. Delete .venv (rm -rf .venv) and re-run; check the pip output above." >&2
    exit 1
  fi
else
  # WHY find_spec and not `import pynput`: importing pynput on Linux connects
  # to the X server at once and fails without DISPLAY (over SSH, in CI), which
  # says nothing about whether the install worked.
  if ! .venv/bin/python -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("pynput") else 1)' 2>/dev/null; then
    echo "install.sh: pynput still cannot be found in .venv. Delete .venv (rm -rf .venv) and re-run; check the pip output above." >&2
    exit 1
  fi
fi

if [[ "$OS" == "Darwin" ]]; then
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
else
  # WHY ask the engine's own detector instead of re-testing XDG_SESSION_TYPE
  # here: this script used to check that one variable, and missed the sessions
  # that set WAYLAND_DISPLAY and leave XDG_SESSION_TYPE unset (sway and
  # Hyprland started from a text console do exactly that) — so the user got no
  # warning at all. One detector, one answer, in both places.
  session_note="$(.venv/bin/python -c '
import sys
sys.path.insert(0, "engine")
from backends import linux_session
v = linux_session.check()
print("OK" if v.ok else "BLOCKED")
print(v.message or "")
print(v.remedy or "")
' 2>/dev/null || true)"

  if [[ -n "$session_note" ]]; then
    session_state="$(printf '%s\n' "$session_note" | sed -n 1p)"
    session_message="$(printf '%s\n' "$session_note" | sed -n 2p)"
    session_remedy="$(printf '%s\n' "$session_note" | sed -n 3p)"
    if [[ "$session_state" == "BLOCKED" ]]; then
      echo
      echo "WARNING: $session_message"
      echo
      echo "  $session_remedy"
      echo
      echo "  The engine will start and serve its console, but it refuses to run the emulation"
      echo "  loop in this state rather than report RUNNING while moving nothing."
    elif [[ -n "$session_message" ]]; then
      echo
      echo "Note: $session_message"
    fi
  fi

  if ! command -v wmctrl >/dev/null 2>&1; then
    echo
    echo "Note: wmctrl is not installed (optional). The engine uses it to count open windows for Alt+Tab;"
    wmctrl_line="$(pkg_install_command "$PKG_MANAGER" wmctrl || true)"
    if [[ -n "$wmctrl_line" ]]; then
      echo "      without it the count falls back to 5. Install it with:  $wmctrl_line"
    else
      echo "      without it the count falls back to 5. The package is called wmctrl on every distribution"
      echo "      that carries it."
    fi
  fi
  cat <<'EOF'

Installed. The engine needs an X11 session (see the Wayland warning above if it was printed).

Then start it with:  ./run.sh
EOF
fi
