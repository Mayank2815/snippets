# Keep Alive — cheatsheet

## Commands

```bash
cd tools/keep-alive
npm install && npm --prefix ui install   # once
npm run dev                              # terminal 1: API on 4311, reloads on change
npm --prefix ui run dev                  # terminal 2: UI on 5311, proxies /api → 4311
npm run typecheck                        # server types
npm test                                 # keeper + routes + store, fake instance
npm run build                            # dist/ and ui/dist/
npm start                                # compiled server, serves the UI on 4311

docker compose up -d --build             # on the VM
docker compose logs -f keep-alive
docker compose ps                        # "healthy" once /healthz answers
ssh -N -L 4311:127.0.0.1:4311 user@your-host   # then http://localhost:4311
```

## Ports

| | API | dev UI |
|---|---|---|
| keep-alive | **4311** | 5311 |
| task-notif (for reference) | 4310 | 5310 |

## Endpoints

```bash
curl -s localhost:4311/healthz
curl -s localhost:4311/api/keeper | jq
curl -s -X PUT localhost:4311/api/keeper -H 'content-type: application/json' -d '{"pingMinutes":5,"hours":8}'
curl -s -X POST localhost:4311/api/keeper/instances -H 'content-type: application/json' \
     -d '{"url":"https://client-24.qa.expertly.cloud/automation-designer","label":"QA24C"}'
curl -s -X POST localhost:4311/api/keeper/instances/client-24-qa-expertly-cloud/start -H 'content-type: application/json' -d '{}'
curl -s -X POST localhost:4311/api/keeper/instances/client-24-qa-expertly-cloud/check
curl -s -X POST localhost:4311/api/keeper/instances/client-24-qa-expertly-cloud/stop
curl -s -X DELETE localhost:4311/api/keeper/instances/client-24-qa-expertly-cloud
```

With `DASHBOARD_PASSWORD` set, add `-u :$DASHBOARD_PASSWORD` (the username is ignored).

The id is a slug of the host: lower-case, non-alphanumerics collapsed to `-`.

## Files

| | |
|---|---|
| kept list + settings | `${DATA_DIR:-./data}/keep-alive.json` (`/app/data` in the container, volume `./data`); `importedAt` in it records the one-off import |
| environment | `.env` next to `docker-compose.yml` on the VM — gitignored, never committed; `.env.example` is the template |
| credentials | only `DASHBOARD_PASSWORD`, in that `.env`. Nothing else is secret: the management API is called without credentials, the same way the maintenance page's own Start button does |
| legacy import | `deploy/deploy.sh` snapshots task-notif's `store.json` to `data/legacy-task-notif-store.json` before the stack restarts; `deploy/docker-compose.yml` sets `LEGACY_TASK_NOTIF_STORE=/app/data/legacy-task-notif-store.json`. Runs once (`importedAt`), instances only. The snapshot must predate the new task-notif, which erases the block on its next write |

## Constants (in `src/keeper.ts`)

| | value | why |
|---|---|---|
| `TICK_MS` | 1 min | the ping interval is in minutes; ticking faster gains nothing |
| `START_COOLDOWN_MS` | 10 min | the management API's own cooldown; asking sooner earns a "wait" |
| `BOOT_POLL_MS` | 1 min | after Start, look every minute so the dashboard sees it come up |
| `BOOT_WINDOW_MS` | 15 min | a boot takes 5–10; fifteen covers a slow one |
| `FETCH_TIMEOUT_MS` | 20 s | slower than this is not "up" in any useful sense |
| `pingMinutes` (setting) | 5 | three pings can fail before the 20-minute idle stop fires |
| `hours` (setting) | 8 | one press of Start should not keep a server up all night |

## Reading a row

- **running** · `up · activity ping HTTP 401 (reached the server)` — healthy; the 401 is the
  session endpoint answering, which is all that matters.
- **running** · `… (cached — did not reach the server)` — the activity path is being served
  from the edge cache; change it to something the app answers itself.
- **starting** · `start requested — checked every minute until it is up` — Start was
  pressed; give it five to ten minutes.
- **stopped** · `down, but the management API for it could not be found` — the wrapper
  could not be parsed; open the URL yourself and press Start on the maintenance page once,
  then Check now.
- **unreachable** — DNS or connection failure; the URL is probably wrong.
