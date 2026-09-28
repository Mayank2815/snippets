# Keep Alive — lessons

Dated notes on things that went wrong or were non-obvious, pulled from the code's own
comments and the history it came with. Newest first.

## 2026-09-28 — review findings on the extraction

- **`hours` was taken off the request body unchecked.** `{"hours":1e999}` parses to
  `Infinity`, `new Date(now + Infinity).toISOString()` throws, and Express 4 does not catch
  a rejection from an async handler — the whole process died on one request. The Start body
  now goes through `KeepAliveSchema.shape.hours`, the same as the settings PUT, so it is a
  400 instead. Every number off the wire goes through the schema; there is no "it is just a
  number" exception.
- **Anyone who can add an instance can make the keeper send requests.** The keeper fetches
  the URL it is given and `POST`s to whatever `API_BASE` it scrapes from the maintenance
  page it finds. `file:///etc/passwd` passed `z.string().url()`, gave an empty slug and a
  500 with a ZodError body. Now only `http(s)` can be added, and a scraped API base that is
  not `http(s)` is dropped. That is as far as the code can go — the management API really is
  on a different host from the instance — so the loopback bind and `DASHBOARD_PASSWORD`
  are the real boundary (README, "Security").
- **The legacy import guard was "the list is empty".** Delete every instance, restart with
  `LEGACY_TASK_NOTIF_STORE` still set (the deploy stack leaves it set), and the rows came
  back. The guard is now `importedAt` in `keep-alive.json`. The import also used to write
  the whole legacy block, overwriting settings tuned here; it now merges instances only.

## 2026-09-28 — extraction from task-notif

- **The store moved, and the old copy is erased on task-notif's next write.** The kept
  list used to be `config.keepAlive` inside task-notif's `store.json`; it is now
  `keep-alive.json` in this service's own `DATA_DIR`. This was first written up as "the new
  task-notif simply ignores the old key, nothing breaks". That is wrong in the way that
  matters: task-notif parses through a zod schema that strips unknown keys, and every write
  path — `setConfig`, `recordRun` on each scheduled send, the dismissals — persists the
  stripped object. The block is gone within hours of the new task-notif starting, so the
  snapshot for the import (`LEGACY_TASK_NOTIF_STORE`) has to be taken **before** task-notif
  is redeployed. `deploy/deploy.sh` does that; a by-hand deploy has to remember to.
- **Settings patches must not carry `undefined`.** `setKeepAlive({ ...stored, ...patch })`
  followed by a schema parse would let an explicit `undefined` in the patch shadow the
  stored value and hand the field back to its default. The store strips undefined keys
  before merging. (zod's own object output omits absent optional keys, so the PUT route
  was already safe; the store guards it anyway.)
- **Relative URLs everywhere in the UI.** So the page can be served behind a reverse-proxy
  prefix such as `/keep-alive/`: Vite `base: './'`, and fetches are `api/keeper…`, never
  `/api/keeper…`. The prefix must end in a slash, and the failure when it does not is a
  blank page: at `/keep-alive` (no slash) the browser resolves `./assets/…` and
  `api/keeper` against `/`, outside the prefix, so the bundle 404s and nothing renders. The
  workbench proxy redirects `/keep-alive` to `/keep-alive/` for that reason.
- **Ports.** task-notif is 4310 (dev UI 5310); this is 4311 (dev UI 5311). Same VM,
  next free port.
- **Tests set `DATA_DIR` before importing the store.** The store resolves its directory
  once at import time, so every test file sets the env var and then `await import`s. Each
  test file runs in its own process under `node --test`, so they cannot see each other's
  store.

## 2026-09-23 — the boot poll

With a ten-minute ping interval the dashboard sat on "stopped — start requested" for the
whole boot, and only moved when someone pressed Check now. After Start is pressed the
instance is now looked at every minute (`BOOT_POLL_MS`) for up to fifteen minutes
(`BOOT_WINDOW_MS`; a boot takes five to ten, fifteen covers a slow one), whatever the
configured interval, and the row flips to **starting** the moment the Start request is
accepted rather than waiting for the next look.

## 2026-09-23 — a page fetch is a CloudFront cache hit, not activity

An instance pinged by its page URL alone still stopped after twenty minutes: every one of
those pings was answered by the edge cache and the server saw nothing. The fix is
`activityPath` — a path the app answers itself (`/rest/api/users/isMySessionActive` by
default), fetched on every ping with a `?keepalive=<timestamp>` cache-buster and
`cache-control: no-cache`. The `x-cache` response header says whether the edge passed it
on, and that is shown in the row ("reached the server" / "cached — did not reach the
server") so a wrong path is visible rather than silently useless. A 401 from that path is
fine — it still reached the server.

## Undated, from the code

- **The management API has a cooldown.** Asking it to start an instance more often than
  every ten minutes (`START_COOLDOWN_MS`) only earns a "wait N minutes" answer, so the keeper
  does not ask again inside that window even if the instance still looks stopped.
- **The management API base differs per environment** and is never configured: it is read
  out of the `const API_BASE = '...'` line in the environment's maintenance page, which the
  maintenance wrapper's iframe points at. The app name comes from that page's file name.
  Until an instance has been seen down once, neither is known, and the row says so.
- **A fresh press of Start means "check now".** `keep()` clears the instance's in-memory
  runtime before pinging, so the ping is not skipped because a recent one is on record.
- **Twenty seconds is the fetch timeout.** A page that takes longer than that is not "up"
  in any useful sense.
- **The ping interval is measured in minutes, so a one-minute tick is precise enough.**
  There is no point ticking faster.

## Inherited from task-notif (still true here)

- **Volume ownership.** A mounted Docker volume arrives with the host's ownership, which
  overrides whatever the image chowned at build time, so a non-root container cannot write
  its store — the first build without the entrypoint `chown` failed with `EACCES` on the
  very first write. `docker-entrypoint.sh` starts as root only long enough to chown
  `DATA_DIR`, then `su-exec`s to `node`; `assertDataDirWritable()` fails loudly at boot with
  the uid and the fix if it is still wrong.
- **`/healthz` before auth.** A platform health check sends no credentials; a 401 would
  make the host think the service is dead and restart it forever.
- **Say something useful on `EADDRINUSE`.** A stale process holding the port otherwise
  makes a supervisor crash-loop in silence; the server prints the `lsof` command to find it.
