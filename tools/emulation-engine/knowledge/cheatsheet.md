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
| GET     | `/status` | `{"status": "IDLE"}` or `{"status": "RUNNING"}`       |
| POST    | `/start`  | `{"success": true, "message": "Stabilized Engine Activated"}` (or "Engine confirmed running") |
| POST    | `/stop`   | `{"success": true, "message": "Stabilized Engine Deactivated"}` or `{"success": false, "message": "Already idle"}` |
| OPTIONS | any       | 200 with CORS headers (preflight)                     |
| *       | other     | 404 `{"error": "not found"}`                          |

Every response carries `Access-Control-Allow-Origin: *`,
`Access-Control-Allow-Methods: GET, POST, OPTIONS` and
`Access-Control-Allow-Headers: Content-Type`.

```bash
curl -i http://127.0.0.1:4320/status
curl -i -X OPTIONS http://127.0.0.1:4320/status
curl -X POST http://127.0.0.1:4320/stop      # safe: only clears the flag
curl -X POST http://127.0.0.1:4320/start     # CAREFUL: moves the mouse and presses keys on this Mac
```

## Port and process

- Binds `127.0.0.1:4320` only; override the port with `PORT=<n>`, never the address.
- Who has the port: `lsof -nP -iTCP:4320 -sTCP:LISTEN`
- Stop a stray engine: `kill $(lsof -t -nP -iTCP:4320 -sTCP:LISTEN)`

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
