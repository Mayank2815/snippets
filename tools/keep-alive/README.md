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
```

Then, in two terminals:

```bash
npm run dev                  # terminal 1: API on http://localhost:4311
npm --prefix ui run dev      # terminal 2: UI on http://localhost:5311, proxying /api to 4311
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

On the team VM this tool is one service of the stack in `deploy/docker-compose.yml` at the
repo root; `./deploy/deploy.sh user@host` syncs it, builds it and restarts everything, and
the workbench reaches it as `/keep-alive/`. The commands below run it on its own, for
example on a personal box. The image builds both stages; `data/` is a volume so the kept
list survives redeploys. Do not run the standalone file on the VM next to the stack: both
name their container `keep-alive`.

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

### Security

Whoever can reach the dashboard can make this service send requests on their behalf. Adding
an instance makes the keeper fetch that URL from wherever the service runs, so an attacker
could point it at hosts only the VM can see; and when a fetched page looks like the
maintenance wrapper, the keeper reads a management API address out of it and `POST`s to
that. The service limits the damage — only `http(s)` URLs can be added, and a scraped API
address that is not `http(s)` is ignored — but it cannot tell an internal host from an
external one, because the real management API legitimately lives on a different host from
the instance. The protection is therefore who can reach the port: keep it on loopback (the
compose files do), and set `DASHBOARD_PASSWORD` the moment it is exposed any further than an
SSH tunnel.

**Behind a path prefix.** The built page uses relative URLs throughout (`base: './'` in
Vite, and every API call is `api/keeper…`, not `/api/keeper…`), so a reverse proxy can serve
it under a prefix such as `/keep-alive/` as long as the prefix ends in a slash and the proxy
strips it before forwarding. The slash is not optional: opened at `/keep-alive` without it,
the browser resolves `api/keeper` and `./assets/…` against `/`, outside the prefix, so the
scripts do not load and the page renders blank. The workbench's proxy redirects
`/keep-alive` to `/keep-alive/` for exactly that reason; do the same in any other proxy.

**Volume ownership.** A mounted volume arrives with the host's ownership, which overrides
whatever the image chowned at build time — so a container running as a non-root user cannot
write its own store. `docker-entrypoint.sh` starts as root only long enough to `chown` the
data directory, then `su-exec`s to `node`. The app itself never runs as root.

### Bringing the kept list over from task-notif

Until 28 September 2026 the list lived inside task-notif's `store.json`, under a `keepAlive`
key. This service can copy it out once so nobody has to re-add every instance by hand — but
the copy has to come from a **snapshot taken before the new task-notif runs**. task-notif
parses its store through a zod schema that strips keys it does not know, and every one of
its write paths (including the run it records on each scheduled send) persists the stripped
object. So the `keepAlive` block is gone from the live `store.json` on task-notif's next
write, within hours of deploying it, and there is nothing left to import.

**The supported path is the repo's deploy stack.** `deploy/deploy.sh` copies
`tools/task-notif/data/store.json` to `tools/keep-alive/data/legacy-task-notif-store.json`
before it restarts anything (only when there is no `keep-alive.json` yet, so a re-deploy
never overwrites a snapshot), and `deploy/docker-compose.yml` sets
`LEGACY_TASK_NOTIF_STORE=/app/data/legacy-task-notif-store.json`. On the first boot the log
shows `[store] imported N instance(s) from /app/data/legacy-task-notif-store.json`; on every
later boot the variable is still set and does nothing.

What the import does and does not do:

- It copies **only the instances**. The three settings stay whatever this service already
  has, so a `pingMinutes` or `activityPath` you tuned here is not overwritten. A row that is
  already here wins over the legacy copy with the same id.
- It runs **once**, recorded as `importedAt` in `keep-alive.json`. It is not "runs while the
  list is empty": deleting every instance and restarting does not bring them back.
- It only reads the snapshot. It never modifies it, or task-notif's file.

By hand, outside the deploy stack, it is the same variable pointing at a copy you took
yourself: `cp ../task-notif/data/store.json data/legacy-task-notif-store.json` **before**
starting the new task-notif, then `LEGACY_TASK_NOTIF_STORE=./data/legacy-task-notif-store.json npm start`.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `4311` | HTTP port. 4310 is task-notif's, so this is the next one along. |
| `DATA_DIR` | `./data` | Where `keep-alive.json` lives. Relative to the working directory — `/app` in the container, so `./data` is `/app/data`, the mounted volume. |
| `DASHBOARD_PASSWORD` | unset | When set, HTTP Basic auth on everything except `/healthz`. Required on any host where the port is public. |
| `LEGACY_TASK_NOTIF_STORE` | unset | Path to a snapshot of task-notif's `store.json` for the one-off import above. The deploy stack sets it. |

## API

All under `/api`; the page is the only client, but nothing stops a script.

| Method | Path | Does |
|---|---|---|
| `GET` | `/api/keeper` | the list with what the last ping saw, plus the three settings |
| `PUT` | `/api/keeper` | change any of `pingMinutes`, `hours`, `activityPath` |
| `POST` | `/api/keeper/instances` | `{ url, label? }` — add one; `http(s)` only; the id is a slug of the host |
| `DELETE` | `/api/keeper/instances/:id` | remove one |
| `POST` | `/api/keeper/instances/:id/start` | keep it for `hours` (body `{ hours? }`, 0.5–24) and look at it now |
| `POST` | `/api/keeper/instances/:id/stop` | stop keeping it |
| `POST` | `/api/keeper/instances/:id/check` | look at it now without changing whether it is kept |

A bad body is a 400 with `{ error }`; an unknown `:id` is a 404 on every per-instance route,
`DELETE` included; anything else under `/api` is a JSON 404 rather than the page.

`GET /healthz` answers before auth, for Docker's health check.

More on how a ping works, and what has gone wrong before, in `knowledge/`.
