# Cheatsheet

## Commands

**macOS and Linux**

```bash
cd tools/emulation-engine
./install.sh                 # create .venv, install the platform's input library, print the next steps
./run.sh                     # start engine, wait for /status, open the console, Ctrl-C to stop
./run.sh --no-open           # same without opening a browser
PORT=4321 ./run.sh           # run on another port
make                         # list targets
make install | run | test    # same as the scripts; test runs the engine tests + engine/test_metrics.py
make bundle                  # emulation-engine-<YYYYMMDD>.zip of this folder minus .venv

.venv/bin/python engine/engine.py         # start the server by hand (foreground)
python3 engine/test_metrics.py            # offline cadence simulator, needs no input library
python3 -m unittest discover -s engine/tests -v   # engine tests (fake backend, no input)
ENGINE_BACKEND=fake .venv/bin/python engine/engine.py   # real server, no input generated
```

**Windows (PowerShell)**

```powershell
cd tools\emulation-engine
.\install.ps1                      # create .venv, install pynput
.\run.ps1                          # start engine, wait for /status, open the console, Ctrl-C to stop
.\run.ps1 -NoOpen                  # same without opening a browser
$env:PORT = 4321; .\run.ps1        # run on another port
Set-ExecutionPolicy -Scope Process Bypass   # once per window, if Windows blocks the .ps1

.\.venv\Scripts\python engine\engine.py
.\.venv\Scripts\python -m unittest discover -s engine/tests -v
```

There is no `make` on Windows — use the two `.ps1` scripts directly.

## Backends

| `sys.platform` | Backend | How input is posted |
|----------------|---------|---------------------|
| `darwin`       | `quartz` | pyobjc Quartz event taps (`CGEventPost` → `kCGHIDEventTap`) |
| `win32`        | `pynput` | SendInput |
| `linux`        | `pynput` | X11 XTest (**not** Wayland) |
| any            | `fake`   | nothing — records calls, for tests and CI |

```bash
ENGINE_BACKEND=fake|quartz|pynput   # override the automatic choice
ENGINE_FAST=1                       # scale every sleep by 0.01 (tests only)
ENGINE_ALLOW_WAYLAND=1              # Linux: run on Wayland anyway (XWayland windows only);
                                    # the warning still shows in /status and the console
```

`GET /status` reports the live one, and the console header shows
`backend: <name> on <platform>`.

## Endpoints (default base `http://127.0.0.1:4320`)

| Method  | Path      | Response                                              |
|---------|-----------|-------------------------------------------------------|
| GET     | `/`       | the console page (`console/index.html`)               |
| GET     | `/status` | `{"status": "IDLE"\|"RUNNING"\|"STOPPING", "backend": "quartz", "platform": "darwin", "inputWorking": true, "warning": null}` (STOPPING = old loop still finishing its step) |
| POST    | `/start`  | `{"success": true, "message": "Stabilized Engine Activated"}` (or "Engine confirmed running"); **409** `{"success": false, "message": "stopping, try again in a moment"}` while STOPPING; **503** `{"success": false, "message": "<why + fix>", "inputWorking": false}` when input cannot be delivered |
| POST    | `/stop`   | `{"success": true, "message": "Stabilized Engine Deactivated"}`, `{"success": true, "message": "Already stopping"}` or `{"success": false, "message": "Already idle"}` |
| OPTIONS | any       | 200 with CORS headers (preflight)                     |
| *       | other     | 404 `{"error": "not found"}`                          |

`POST /start` and `/stop` require the header `X-Engine-Control: 1` — without
it, or from a browser Origin that is not `http://127.0.0.1[:port]` /
`http://localhost[:port]`, they answer **403** `{"success": false, ...}`.

CORS headers are sent only when the request has no `Origin` (curl) or an
allowed one: `Access-Control-Allow-Origin: <that origin>` (omitted when there
is no Origin), `Vary: Origin`, `Access-Control-Allow-Methods: GET, POST,
OPTIONS`, `Access-Control-Allow-Headers: Content-Type, X-Engine-Control` and
`Access-Control-Allow-Private-Network: true`. Any other Origin gets none.

```bash
curl -i http://127.0.0.1:4320/status
curl -i -X OPTIONS -H 'Origin: http://localhost:4310' http://127.0.0.1:4320/status
curl -X POST -H 'X-Engine-Control: 1' http://127.0.0.1:4320/stop      # safe: only asks the loop to end
curl -X POST -H 'X-Engine-Control: 1' http://127.0.0.1:4320/start     # CAREFUL: moves the mouse and presses keys on this computer
```

## Port and process

- Binds `127.0.0.1:4320` only; override the port with `PORT=<n>`, never the address.
- Who has the port: `lsof -nP -iTCP:4320 -sTCP:LISTEN` (Windows: `netstat -ano | findstr :4320`)
- Stop a stray engine: `kill $(lsof -t -nP -iTCP:4320 -sTCP:LISTEN)` (Windows: `Stop-Process -Id <pid>`) — SIGTERM is handled like Ctrl-C (loop stopped, modifiers released, "Shutdown complete.")

## Permissions and session type

- **macOS** — System Settings → Privacy & Security → **Accessibility** → enable your terminal app;
  same under **Input Monitoring** if events are dropped; **Automation** → your terminal → System
  Events for the app-count query (optional; falls back to 5). Restart the terminal app after toggling.
- **Windows** — nothing to grant. `Set-ExecutionPolicy -Scope Process Bypass` if PowerShell blocks
  the unsigned `.ps1`, and "More info → Run anyway" on a SmartScreen prompt.
- **Linux** — must be an **X11/Xorg** session. `echo $XDG_SESSION_TYPE` must print `x11`; on
  `wayland` the engine refuses `/start` with 503 and the console shows a red INPUT UNAVAILABLE
  panel (Wayland discards synthetic XTest input). Fix: log out → click your name → gear icon →
  "Ubuntu on Xorg" → log in. Packages, printed by `install.sh` for whichever package manager
  this box has: `sudo apt install build-essential python3-dev python3-venv` (Debian/Ubuntu),
  `sudo dnf install gcc python3-devel` (Fedora), `sudo pacman -S base-devel python` (Arch),
  `sudo zypper install gcc python3-devel` (openSUSE); an unrecognised manager gets the
  requirements in words and no command. `evdev` has no prebuilt wheel for any architecture,
  x86_64 included, so the compiler is always needed. `wmctrl` is optional (window count;
  falls back to 5).

```bash
echo $XDG_SESSION_TYPE                 # must print x11
xdotool mousemove 500 500              # does synthetic input work at all, outside this tool?
xinput test-xi2 --root                 # every key/motion event the X server receives
                                       # (xev -root misses XTest keys — they go to the focused window)
wmctrl -l                              # what visible_app_count() counts
```

## Keys and chords per platform

| Concept          | macOS (`quartz`)                     | Windows / Linux (`pynput`) |
|------------------|--------------------------------------|----------------------------|
| arrows           | key codes 123–126 (L, R, Down, Up)   | `Key.left/right/down/up`   |
| Shift            | key code 56                          | `Key.shift`                |
| Tab              | key code 48                          | `Key.tab`                  |
| app switcher     | Command (55) + Tab, flag `0x100000`  | Alt + Tab                  |
| next browser tab | Cmd+Option+Right, flags `0x180000`   | Ctrl+Tab                   |

Flag values are Quartz masks, not key codes: `1048576` = `kCGEventFlagMaskCommand`
(0x100000), `1572864` = Command|Alternate (0x180000).

## Testing it without moving your mouse

```bash
make test                                     # unit tests (fake backend) + cadence simulator
ENGINE_BACKEND=fake ENGINE_FAST=1 python3 engine/engine.py    # server + loop, zero real input
```

The Linux path can be exercised end to end in Docker against something close to
a real desktop — which is what a bare `Xvfb` as root is not. Build from
`ubuntu:24.04` (and `22.04`), add a **non-root** user with sudo, install
`xvfb x11-utils xdotool wmctrl openbox xterm x11-apps`, and deliberately leave
out `python3-venv`, `python3-pip`, `build-essential` and `python3-dev` so the
install path is tested as a fresh machine actually experiences it. Then:

```bash
Xvfb :99 -screen 0 1440x900x24 -ac &   # xvfb-run HANGS in slim images: its
export DISPLAY=:99                     # SIGUSR1 handshake never fires
openbox & xterm & xclock &             # a WM and real windows, so wmctrl counts something
./install.sh && ./run.sh --no-open
```

Synthetic input inside Xvfb touches nothing real. To prove the input actually
lands, use tools that are not this codebase: `xdotool getmouselocation` sampled
over several seconds for the pointer, and `xinput test-xi2 --root` for keys.
**`xev -root` will report zero KeyPress events even when everything works** —
XTest keys go to the focused window, and `xev -root` only sees them when focus
is on the root window. Decode the `detail:` numbers to keysyms with
`xmodmap -pke`.
