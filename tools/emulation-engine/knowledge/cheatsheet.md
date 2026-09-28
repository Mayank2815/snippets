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
```

`GET /status` reports the live one, and the console header shows
`backend: <name> on <platform>`.

## Endpoints (default base `http://127.0.0.1:4320`)

| Method  | Path      | Response                                              |
|---------|-----------|-------------------------------------------------------|
| GET     | `/`       | the console page (`console/index.html`)               |
| GET     | `/status` | `{"status": "IDLE"\|"RUNNING"\|"STOPPING", "backend": "quartz", "platform": "darwin"}` (STOPPING = old loop still finishing its step) |
| POST    | `/start`  | `{"success": true, "message": "Stabilized Engine Activated"}` (or "Engine confirmed running"); **409** `{"success": false, "message": "stopping, try again in a moment"}` while STOPPING |
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
- **Linux** — must be an **X11/Xorg** session; Wayland does not deliver synthetic XTest input to
  native Wayland windows. `wmctrl` is optional (window count; falls back to 5).

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

The Linux path can be exercised end to end in Docker with a headless X server:
`python:3.12-slim` + `xvfb xauth wmctrl`, start `Xvfb :99`, `export DISPLAY=:99`,
then `./install.sh` and `engine/engine.py`. Synthetic input inside Xvfb touches
nothing real.
