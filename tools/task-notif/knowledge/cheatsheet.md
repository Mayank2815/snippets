# task-notif: cheatsheet

Run everything from `tools/task-notif/`.

## Commands

| Command | Does |
|---|---|
| `npm install && npm --prefix dashboard install` | Install server and dashboard dependencies |
| `cp .env.example .env` | Then fill in the tokens (see "Where to find") |
| `npm run dev` | API and scheduler on :4310, restarts on change |
| `npm --prefix dashboard run dev` | Dashboard on :5310, proxies `/api` to :4310 |
| `npm run build` | Compile server (`dist/`) and dashboard (`dashboard/dist/`) |
| `npm start` | Run the compiled server |
| `npm test` | Unit tests (`src/__tests__/*.test.ts`, Node's built-in runner via tsx) |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run dry-run` | What the morning reminder would contain; sends nothing |
| `npm run digest-preview` | What tonight's digest would contain; sends nothing |
| `npm run send-now` | Send the reminder now |
| `npm run send-now digest` | Send the digest now |
| `npm run probe` | Dump live Teamwork API shapes |
| `npm run deploy -- user@host` | Same as `./scripts/deploy.sh user@host` |

## Ports

| Port | What |
|---|---|
| 4310 | The server: API at `/api`, dashboard at `/`, `/healthz`. Bound to loopback on the VM. |
| 5310 | Vite dev server for the dashboard (development only) |

## Endpoints

All under `/api` except `/healthz`. Defined in `src/server/routes.ts` and `src/server/index.ts`.

| Method | Path | Does |
|---|---|---|
| `GET` | `/healthz` | Liveness. `{ ok, uptimeSeconds }`. Declared before auth. |
| `GET` | `/api/config` | Config with tokens redacted, plus `nextRun` |
| `PUT` | `/api/config` | Partial config update. A blank token means "leave it alone". Reschedules. |
| `GET` | `/api/people` | Teamwork people with handle evidence. `?refresh=1` rescans comments. |
| `POST` | `/api/recipients` | Add a recipient (needs `teamworkUserId` and at least one handle) |
| `DELETE` | `/api/recipients/:id` | Remove a recipient |
| `GET` | `/api/rules` | Rules with `enabled` flags |
| `GET` | `/api/status` | Teamwork and Slack connection checks, `nextRuns`, last 10 runs |
| `POST` | `/api/preview` | Run the scan and return what the reminder would hold |
| `GET` | `/api/dismissals` | Items marked Done (last 100), with whether undo is still open |
| `POST` | `/api/dismissals/undo` | `{ recipientId, key }`. Removes the dismissal, restores the Slack row if the window is open. |
| `POST` | `/api/report` | `{ from, to }` dates. Starts a date-range report job; returns `202 { id }`. |
| `GET` | `/api/report/:id` | Job status, last 8 progress lines, result when done. In memory only. |
| `POST` | `/api/test-send` | `{ job: reminder \| digest \| weekly, recipientId? }`. Sends now, as a manual run. |

Keep-alive routes (`/api/keeper`, `/api/keeper/instances...`) and the emulation bridge
routes (`/automation/...`) still sit in this server today and are moving to their own tools.

## Environment variables

From `.env.example`. Tokens live only in `.env`; everything else is in the dashboard.

| Variable | Required | What |
|---|---|---|
| `TEAMWORK_SITE_URL` | yes | `https://<site>.teamwork.com` (also editable in the dashboard) |
| `TEAMWORK_API_TOKEN` | yes | Shared Teamwork token. HTTP Basic username, password `x`. |
| `SLACK_BOT_TOKEN` | yes | `xoxb-`. Scopes `chat:write`, `users:read`, `users:read.email`. Sends the DMs. |
| `SLACK_APP_TOKEN` | for buttons | `xapp-`. Socket Mode only. Without it the buttons render but do nothing. |
| `SLACK_USER_TOKEN_<ID>` | per person, optional | `xoxp-`. Mention search. Scopes `search:read`, `channels:history`, `groups:history`, `im:history`, `mpim:history`. Wins over the dashboard value. |
| `TEAMWORK_USER_TOKEN_<ID>` | per person, optional | That person's own Teamwork token. Reads their boards; makes Reply, Complete and Move-date post as them. Wins over the dashboard value. |
| `GEMINI_API_KEY` | optional | Lets Gemini phrase the stand-up summary when `standupSummaryEnabled` is on |
| `DASHBOARD_PASSWORD` | on public hosts | HTTP Basic auth for the dashboard. Not needed behind loopback. |
| `PORT` | no | Default 4310 |
| `DATA_DIR` | no | Default `./data`; `/app/data` in Docker |
| `SUPPRESS_CATCHUP` | no | `1` stops a restart from sending a missed slot |

`<ID>` is the recipient id upper-cased with non-alphanumerics as underscores:
`alex-kim` becomes `SLACK_USER_TOKEN_ALEX_KIM`.

## Deploy

VM with Docker (the normal way):

```bash
./scripts/provision-host.sh ubuntu@<ip>          # once: Docker, rsync, 2 GB swap
./scripts/deploy.sh ubuntu@<ip>                  # rsync to /opt/task-notif, build, restart, wait for healthy
ssh -N -L 4310:127.0.0.1:4310 ubuntu@<ip>        # then open http://localhost:4310
```

On the VM:

```bash
cd /opt/task-notif
docker compose up -d --build
docker compose logs --tail 50 -f
docker inspect --format '{{.State.Health.Status}}' task-notif
```

Mac with launchd (fallback, only while the Mac is awake):

```bash
./scripts/install-launchd.sh
launchctl bootout gui/$UID/com.tasknotif.agent     # stop
tail -f ~/Library/Logs/task-notif/server.err.log
```

Render: point a Blueprint at the repo; `render.yaml` does the rest. Paid plan, disk at
`/app/data`, secrets set in the Render dashboard.

## Where to find

| Thing | Where |
|---|---|
| Teamwork token instructions | `README.md`, "Setup", "Teamwork token": avatar, Edit My Details, API & Mobile |
| Slack app and token instructions | `README.md`, "Setup", "Slack token"; `slack-app-manifest.yaml` has the scopes. Turn Socket Mode on in the app settings to get the `xapp-` token. |
| Finding a person's `@handle` | Dashboard, Recipients, Add person. Or `README.md`, "Finding someone's handle". |
| The store | `data/store.json` (mode 0600). Config, dismissals, last 50 runs. |
| Adding a rule | `README.md`, "Adding a new rule"; `src/rules/index.ts` |
| Done / blocker keyword lists | `DONE_MARKERS`, `BLOCKER_MARKERS` in `src/digest.ts` |
| Retry and catch-up timings | `src/scheduler/index.ts` |
| Logs on the VM | `docker compose logs` in `/opt/task-notif` |
| Logs on a Mac | `~/Library/Logs/task-notif/` |
