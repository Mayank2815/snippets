# Keep Alive

Keeps dev and QA instances awake while someone is working on them.

DevOps stop any instance that has seen no traffic for twenty minutes, and it takes five to
ten minutes to come back. In the middle of a fix that is exactly long enough to lose the
instance between writing a change and checking it. So while an instance is "kept", this
pings it every few minutes — and if it goes down anyway, presses the same Start button the
maintenance page shows, so nobody has to.

An instance is kept only for a fixed number of hours from each press of Start: the idle
stop exists to save money overnight, and a forgotten toggle should not defeat it.

It started life as a panel inside `task-notif`. It is its own service now so the two can be
deployed, restarted and upgraded without touching each other.

## What you see

One page. Paste the URL of the dev or QA server you are working on, press **Start**, and
the row tells you what the last look saw (up, starting, stopped, unreachable), when it was
last pinged, when it will be next, and until when it is being kept. **Stop** ends the
keeping early; **Check now** looks immediately; **Remove** takes the row off the list.

Three settings underneath:

- **Ping every** — minutes between pings. Five by default: room for three failed pings
  before the twenty-minute idle stop would fire.
- **Keep for** — hours per press of Start. Eight by default.
- **Activity path** — an app path fetched on every ping so the request reaches the server
  itself. The page URL alone is answered by CloudFront's cache and never counts as
  activity (see `knowledge/lessons.md`).

## First run

```bash
cd tools/keep-alive
npm install
npm --prefix ui install
cp .env.example .env         # nothing in it is required locally
npm run dev                  # API on http://localhost:4311
npm --prefix ui run dev      # UI on http://localhost:5311, proxying /api to 4311
```

In development the UI is served by Vite on 5311. In production `npm run build` compiles
both and the server serves the built UI from `/` on 4311.

## Running

| Command | What it does |
|---|---|
| `npm run dev` | API with reload |
| `npm run build` | compile the server to `dist/` and the UI to `ui/dist/` |
| `npm start` | run the compiled server (serves the UI too) |
| `npm run typecheck` | server types only; the UI is type-checked by its own build |
| `npm test` | keeper, routes and store tests, against a fake instance |

The kept list and the three settings live in `${DATA_DIR:-./data}/keep-alive.json`. What the
last ping saw is in memory only: after a restart every kept instance is looked at again on
the first tick, which is what you would want anyway.

## Deploying

Docker Compose on the same VM as task-notif. The image builds both stages; `data/` is a
volume so the kept list survives redeploys.

```bash
cd tools/keep-alive
cp .env.example .env                          # set DASHBOARD_PASSWORD if the port is ever public
docker compose up -d --build
docker compose ps                             # healthy once /healthz answers
ssh -N -L 4311:127.0.0.1:4311 user@your-host  # then open http://localhost:4311
```

The port is bound to loopback on the host on purpose. There is no login unless
`DASHBOARD_PASSWORD` is set, and the page can press Start on someone's server, so it must
not be reachable from the internet — reach it over the SSH tunnel above, or put it behind
a reverse proxy that handles authentication.

**Behind a path prefix.** The built page uses relative URLs throughout (`base: './'` in
Vite, and every API call is `api/keeper…`, not `/api/keeper…`), so a reverse proxy can serve
it under a prefix such as `/keep-alive/` as long as the prefix ends in a slash and the proxy
strips it before forwarding.

**Volume ownership.** A mounted volume arrives with the host's ownership, which overrides
whatever the image chowned at build time — so a container running as a non-root user cannot
write its own store. `docker-entrypoint.sh` starts as root only long enough to `chown` the
data directory, then `su-exec`s to `node`. The app itself never runs as root.

### Bringing the kept list over from task-notif

Until 28 September 2026 the list lived inside task-notif's `store.json`, under a `keepAlive`
key. task-notif ignores that key now (its schema no longer knows it), and this service can
copy it out once so nobody has to re-add every instance by hand.

On the first boot only:

1. In `docker-compose.yml`, uncomment the read-only mount of task-notif's data directory
   (`../task-notif/data:/legacy:ro`).
2. In `.env`, set `LEGACY_TASK_NOTIF_STORE=/legacy/store.json`.
3. `docker compose up -d --build`, then `docker compose logs` — you should see
   `[store] imported N instance(s) and settings from /legacy/store.json`.
4. Remove the mount and the variable again. They are harmless if left (the import only
   runs while this service's own list is empty), but there is no reason to keep task-notif's
   data mounted here.

Outside Docker it is the same variable pointing at the file directly, e.g.
`LEGACY_TASK_NOTIF_STORE=../task-notif/data/store.json npm start`.

The import only reads task-notif's file. It never modifies it.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `4311` | HTTP port. 4310 is task-notif's, so this is the next one along. |
| `DATA_DIR` | `./data` | Where `keep-alive.json` lives. `/app/data` in the container, mounted as a volume. |
| `DASHBOARD_PASSWORD` | unset | When set, HTTP Basic auth on everything except `/healthz`. Required on any host where the port is public. |
| `LEGACY_TASK_NOTIF_STORE` | unset | Path to task-notif's `store.json` for the one-off import above. |

## API

All under `/api`; the page is the only client, but nothing stops a script.

| Method | Path | Does |
|---|---|---|
| `GET` | `/api/keeper` | the list with what the last ping saw, plus the three settings |
| `PUT` | `/api/keeper` | change any of `pingMinutes`, `hours`, `activityPath` |
| `POST` | `/api/keeper/instances` | `{ url, label? }` — add one; the id is a slug of the host |
| `DELETE` | `/api/keeper/instances/:id` | remove one |
| `POST` | `/api/keeper/instances/:id/start` | keep it for `hours` (body `{ hours? }`) and look at it now |
| `POST` | `/api/keeper/instances/:id/stop` | stop keeping it |
| `POST` | `/api/keeper/instances/:id/check` | look at it now without changing whether it is kept |

`GET /healthz` answers before auth, for Docker's health check.

More on how a ping works, and what has gone wrong before, in `knowledge/`.
