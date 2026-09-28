# Keep Alive — lessons

Dated notes on things that went wrong or were non-obvious, pulled from the code's own
comments and the history it came with. Newest first.

## 2026-09-28 — extraction from task-notif

- **The store moved.** The kept list used to be `config.keepAlive` inside task-notif's
  `store.json`; it is now `keep-alive.json` in this service's own `DATA_DIR`. task-notif's
  config schema is a plain zod object, so a `store.json` that still carries the old key is
  simply ignored — no migration on that side, nothing breaks. The one-off import
  (`LEGACY_TASK_NOTIF_STORE`) is how the list comes across; it only reads.
- **Settings patches must not carry `undefined`.** `setKeepAlive({ ...stored, ...patch })`
  followed by a schema parse would let an explicit `undefined` in the patch shadow the
  stored value and hand the field back to its default. The store strips undefined keys
  before merging. (zod's own object output omits absent optional keys, so the PUT route
  was already safe; the store guards it anyway.)
- **Relative URLs everywhere in the UI.** So the page can be served behind a reverse-proxy
  prefix such as `/keep-alive/`: Vite `base: './'`, and fetches are `api/keeper…`, never
  `/api/keeper…`. The prefix must end in a slash for the relative resolution to work.
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
