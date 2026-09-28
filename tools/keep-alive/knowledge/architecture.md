# Keep Alive — architecture

One Node process (Express, TypeScript, ES modules) and one React page. No database, no
queue, no scheduler library — a `setInterval` and a JSON file.

```
ui/            Vite + React, one panel, built to ui/dist and served by the API
src/server.ts  Express on PORT (4311): /healthz, optional Basic auth, /api, static UI
src/routes.ts  the /api/keeper routes
src/keeper.ts  the Keeper: the loop, the probe, and pressing Start
src/store.ts   ${DATA_DIR}/keep-alive.json, atomic writes, one-off import from task-notif
src/schema.ts  zod schemas for a kept instance and for the store as a whole
```

## The Keeper loop

`Keeper.start()` runs `tick()` immediately and then once a minute (`TICK_MS`). The interval
is `unref`'d so it never keeps the process alive on its own.

Each tick walks the kept list. An instance is *kept* while `keepUntil` is in the future.
For each kept instance the keeper pings it if enough time has passed since the last ping:

- normally `pingMinutes` (default five — three pings can fail before the twenty-minute
  idle stop fires);
- while the instance is **booting** — Start was pressed less than fifteen minutes ago
  (`BOOT_WINDOW_MS`) and it is not yet running — every minute (`BOOT_POLL_MS`), so the
  dashboard sees it come up without anyone pressing Check.

A ping already in flight for an instance is never overlapped (`inFlight`); a second
request for the same id just returns the current view.

## One ping: `probeInstance`

1. `GET` the instance URL, following redirects, twenty-second timeout, a User-Agent that
   is recognisable in a log.
2. If the body is the **maintenance wrapper** — a page whose only content is an iframe of
   `.../maintenance/<APP>.html` — or the status is 503, the instance is down. The
   wrapper's file name is the server-management app name (e.g. `QA9C`).
3. For a down instance, ask `${origin}/_kvs_status?app=<APP>`: `running`, `starting` or
   anything else (stopped). Then fetch the maintenance page itself and read the
   `const API_BASE = '...'` line out of its script — that is the management API for this
   environment, and it differs per environment, so it is learned rather than configured.
4. If the page came back 2xx and is not the wrapper, the instance is **running** — and the
   keeper also calls `touchOrigin`.

### `touchOrigin`: the ping that actually counts

The page URL is a CloudFront cache hit. Fetching it proves the edge is up and tells the
server nothing, so an instance "kept" by its page alone still idles out. Every ping
therefore also fetches `activityPath` (default `/rest/api/users/isMySessionActive`) from the
instance's origin with a `?keepalive=<timestamp>` cache-buster and `cache-control:
no-cache`. The response's `x-cache` header says whether the edge passed it on
(`Miss`/`Error` from CloudFront = reached the server; `Hit` = it did not), and that is
reported in the row's detail so a misconfigured path is visible.

## Pressing Start

When a ping finds a kept instance **stopped**, `autoStart` is on, and no Start was pressed
in the last ten minutes (`START_COOLDOWN_MS` — the management API has its own cooldown and
asking sooner only earns a "wait"), the keeper `POST`s `${API_BASE}/start?app=<APP>`. The
response's `status` is turned into one line for the dashboard: `starting` /
`already_starting` → "start requested — usually 5 to 10 minutes", `already_running`,
`cooldown` with the minutes remaining, or the API's message. A successful request flips the
row to **starting** straight away and enters the boot-poll window above.

If the API base or app name is not known yet (the instance was never seen down before and
the wrapper could not be parsed), the row says so rather than guessing.

## State: what persists and what does not

- **Persisted** (`keep-alive.json`): the list — `id`, `url`, `label`, `keepUntil`, the
  learned `app`, `autoStart` — the three settings `pingMinutes`, `hours`, `activityPath`,
  and `importedAt` (when the one-off import ran, or null). Written on every change via temp
  file + rename, so a crash mid-write cannot truncate it. A corrupt file falls back to
  defaults and is left in place.
- **In memory** (`Runtime` per instance): last ping time, state, detail, last Start time
  and result, the learned API base. Lost on restart; the next tick pings everything kept.

The **id** is a slug of the URL's host (`client-9.qa.example.cloud` →
`client-9-qa-example-cloud`), so adding the same instance by two different pages is one
row.

## The one-off import

Until 28 September 2026 all of this lived under `config.keepAlive` in task-notif's
`store.json`. On a start where `importedAt` is still null, if `LEGACY_TASK_NOTIF_STORE`
names a snapshot of that file, the block is parsed with the same schema and its
**instances** are added to the list here (rows already present win on an id clash); the
settings are left as they are, and `importedAt` is written. The file is only read. Once
`importedAt` is set the import never runs again, however empty the list becomes.

The snapshot matters: the new task-notif's schema strips the `keepAlive` key and every
write persists the stripped object, so the live `store.json` loses the block on task-notif's
next write. `deploy/deploy.sh` copies it aside before the stack restarts.

## The page

`ui/src/KeepAlivePanel.tsx` re-reads `api/keeper` every fifteen seconds so a starting
instance is seen to come up. Every call is a *relative* URL and Vite's `base` is `'./'`, so
the built page works under a reverse-proxy path prefix. The Express server serves
`ui/dist` when it exists and falls back to `index.html` for any other path — except under
`/api`, where the router ends in a JSON 404 so a mistyped endpoint is not answered with the
page and a 200.

## Boundaries

- `/healthz` is registered before Basic auth so a health check with no credentials
  cannot be mistaken for a dead service.
- Only `http(s)` URLs can be added, and a management API base scraped from a maintenance
  page is used only if it is `http(s)` too. The keeper fetches and POSTs on behalf of
  whoever can reach the dashboard, which is why the port stays on loopback.
- The keeper is started only inside `listen`'s callback: a port clash exits before any
  instance is pinged. `SIGINT`/`SIGTERM` stop the loop, let in-flight responses finish
  (`server.close`), and force the exit after five seconds — inside Docker's ten-second
  SIGKILL window.
- The router does not own the keeper — the server does — so tests can hand it one with a
  fake `fetch`.
