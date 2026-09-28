#!/usr/bin/env bash
# Which package manager does this Linux machine use, and what are the packages
# install.sh needs called there?
#
# WHY this is its own file: install.sh used to print one `sudo apt install ...`
# line unconditionally. On Fedora, Arch or openSUSE that line is not merely
# unhelpful — it is wrong, and the user is left staring at an evdev compile
# failure with no idea which package supplies the header it wants. Keeping the
# lookup here, with no side effects at all, lets the tests source this file and
# call every branch with a faked PATH — no container, no root, no distro.
#
# Nothing here runs anything or changes anything; it only answers questions.

# pkg_manager — prints apt, dnf, pacman, zypper, or unknown.
#
# WHY "is the command on PATH" and not /etc/os-release: derivatives are endless
# (Mint, Pop!_OS, Nobara, EndeavourOS, ...) and every one of them keeps its
# parent's package manager. The binary that exists is the fact that matters.
pkg_manager() {
  # apt is tested first so a Debian/Ubuntu machine always takes the path whose
  # wording and package names were already proven there.
  if command -v apt >/dev/null 2>&1 || command -v apt-get >/dev/null 2>&1; then
    echo apt
  elif command -v dnf >/dev/null 2>&1; then
    echo dnf
  elif command -v pacman >/dev/null 2>&1; then
    echo pacman
  elif command -v zypper >/dev/null 2>&1; then
    echo zypper
  else
    echo unknown
  fi
}

# pkg_name <manager> <need> — the package name, or empty if we cannot name one.
#
# The needs are the four things install.sh actually checks for, plus the
# optional wmctrl. They are deliberately abstract: "the Python headers" is
# python3-dev on Debian, python3-devel on Fedora and openSUSE, and part of the
# plain `python` package on Arch.
pkg_name() {
  case "$1:$2" in
    # Debian, Ubuntu, Mint, Pop!_OS and every other apt derivative.
    apt:python)                        echo python3 ;;
    apt:venv)                          echo python3-venv ;;
    apt:compiler|apt:kernel_headers)   echo build-essential ;;
    apt:python_headers)                echo python3-dev ;;
    apt:wmctrl)                        echo wmctrl ;;
    # Fedora, RHEL, CentOS Stream, Rocky, Alma, Nobara.
    # WHY venv is just `python3`: Fedora ships ensurepip inside python3-libs,
    # which the python3 package pulls in — there is no python3-venv to install.
    dnf:python)                        echo python3 ;;
    dnf:venv)                          echo python3 ;;
    dnf:compiler)                      echo gcc ;;
    dnf:kernel_headers)                echo kernel-headers ;;
    dnf:python_headers)                echo python3-devel ;;
    dnf:wmctrl)                        echo wmctrl ;;
    # Arch, Manjaro, EndeavourOS.
    # WHY venv and the headers are both `python`: Arch ships one unsplit python
    # package that already contains ensurepip and Python.h.
    pacman:python)                     echo python ;;
    pacman:venv|pacman:python_headers) echo python ;;
    pacman:compiler)                   echo base-devel ;;
    pacman:kernel_headers)             echo linux-api-headers ;;
    pacman:wmctrl)                     echo wmctrl ;;
    # openSUSE Leap and Tumbleweed, SLES.
    zypper:python)                     echo python3 ;;
    zypper:venv)                       echo python3 ;;
    zypper:compiler)                   echo gcc ;;
    zypper:kernel_headers)             echo linux-glibc-devel ;;
    zypper:python_headers)             echo python3-devel ;;
    zypper:wmctrl)                     echo wmctrl ;;
    *)                                 echo "" ;;
  esac
}

# pkg_install_command <manager> <need>... — the whole line the user should run,
# e.g. "sudo apt install build-essential python3-dev python3-venv". Prints
# nothing and returns 1 when the manager is unknown or nothing can be named,
# which is the caller's cue to describe the requirement in words instead.
#
# Package names are sorted and deduplicated: two different checks can ask for
# the same package (a missing compiler and a missing linux/input.h are both
# build-essential on Debian), and sorting keeps the line identical whatever
# order the checks happened to fire in.
pkg_install_command() {
  local manager="$1"
  shift
  local verb
  case "$manager" in
    apt)    verb="sudo apt install" ;;
    dnf)    verb="sudo dnf install" ;;
    pacman) verb="sudo pacman -S" ;;
    zypper) verb="sudo zypper install" ;;
    *)      return 1 ;;
  esac

  local names=() need name
  for need in "$@"; do
    name="$(pkg_name "$manager" "$need")"
    if [[ -n "$name" ]]; then
      names+=("$name")
    fi
  done
  if [[ ${#names[@]} -eq 0 ]]; then
    return 1
  fi

  # WHY a read loop and not `readarray`: readarray arrived in bash 4, and macOS
  # still ships bash 3.2. The install itself only ever runs this on Linux, but
  # the tests run it wherever `make test` runs, which is usually a Mac.
  local uniq="" line
  while IFS= read -r line; do
    uniq="${uniq:+$uniq }$line"
  done < <(printf '%s\n' "${names[@]}" | sort -u)
  echo "$verb $uniq"
}

# pkg_generic_description <need> — the requirement in plain words, for a
# distribution we could not identify. Naming a package that does not exist is
# worse than naming none, so this is what gets printed instead.
pkg_generic_description() {
  case "$1" in
    python)         echo "Python 3.9 or newer" ;;
    venv)           echo "Python's venv support (the ensurepip module)" ;;
    compiler)       echo "a C compiler (gcc or clang)" ;;
    kernel_headers) echo "the Linux kernel userspace headers (linux/input.h)" ;;
    python_headers) echo "the Python development headers (Python.h)" ;;
    wmctrl)         echo "wmctrl" ;;
    *)              echo "" ;;
  esac
}
