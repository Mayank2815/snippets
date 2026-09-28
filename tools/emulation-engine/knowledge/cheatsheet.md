# Cheatsheet

## Commands

```bash
cd tools/emulation-engine
./install.sh                 # create .venv, install pyobjc Quartz, print the Accessibility steps
./run.sh                     # start engine, wait for /status, open the console, Ctrl-C to stop
./run.sh --no-open           # same without opening a browser
PORT=4321 ./run.sh           # run on another port
make                         # list targets
make install | run | test    # same as the scripts; test runs engine/test_metrics.py
make bundle                  # emulation-engine-<YYYYMMDD>.zip of this folder minus .venv

.venv/bin/python engine/mac_engine.py     # start the server by hand (foreground)
python3 engine/test_metrics.py            # offline cadence simulator, needs no Quartz
```

## Endpoints (default base `http://127.0.0.1:4320`)

| Method  | Path      | Response                                              |
|---------|-----------|-------------------------------------------------------|
| GET     | `/`       | the console page (`console/index.html`)               |
| GET     | `/status` | `{"status": "IDLE"}`, `{"status": "RUNNING"}` or `{"status": "STOPPING"}` (old loop still finishing its step) |
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
curl -X POST -H 'X-Engine-Control: 1' http://127.0.0.1:4320/start     # CAREFUL: moves the mouse and presses keys on this Mac
```

## Port and process

- Binds `127.0.0.1:4320` only; override the port with `PORT=<n>`, never the address.
- Who has the port: `lsof -nP -iTCP:4320 -sTCP:LISTEN`
- Stop a stray engine: `kill $(lsof -t -nP -iTCP:4320 -sTCP:LISTEN)` — SIGTERM is handled like Ctrl-C (loop stopped, Command key released, "Shutdown complete.")

## Permissions

- System Settings → Privacy & Security → **Accessibility** → enable your terminal app.
- Same under **Input Monitoring** if events are dropped.
- **Automation** → your terminal → System Events, for the app-count query (optional; falls back to 5).
- Restart the terminal app after toggling.

## Key codes and flags used

| Constant              | Value     | Meaning                              |
|-----------------------|-----------|--------------------------------------|
| `CORE_DENSE_KEYS`     | 123–126   | Left, Right, Down, Up arrow          |
| `SHIFT_MODIFIER`      | 56        | left Shift                           |
| `TAB_KEY`             | 48        | Tab                                  |
| `COMMAND_KEY`         | 55        | left Command                         |
| `RIGHT_ARROW`         | 124       | Right arrow                          |
| `FLAG_COMMAND`        | 0x100000  | kCGEventFlagMaskCommand              |
| `FLAG_COMMAND_OPTION` | 0x180000  | Command \| Alternate (Cmd+Option)    |
