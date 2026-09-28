# Workbench: lessons

- **2026-09-28.** A tool page that uses absolute paths (`/assets/...`, `fetch('/api/...')`)
  breaks the moment it sits behind a prefix: the browser asks the workbench for `/api/...`
  and nginx has no such route. Relative paths fix it and cost nothing when the tool is
  served alone. Vite: `base: './'`; fetch: `api/...`.
- **2026-09-28.** `location /task-notif/` alone leaves `/task-notif` (no slash) a 404 with a
  confusing empty proxy path. An explicit `location = /task-notif { return 301 /task-notif/; }`
  is the cheapest fix.
- **2026-09-28.** The default `proxy_read_timeout` of 60 s is too short for Task Notif's
  dry-run preview, which scans every task in the workspace. It is raised to 180 s for that
  location only.
- **2026-09-28.** An engine started by the old Express bridge (before the extraction) can
  keep running for days: it answers `/status`, so a plain probe says "idle", but it has no
  console page, so the iframe shows a bare browser error. The tab now also fetches `/` and,
  when that fails, says an older engine is running and gives the `kill $(lsof ...)` command.
  Seen live on the first verification: PID from 2026-09-27 still held 4320.
- **2026-09-28.** A literal host in `proxy_pass` is resolved once when nginx starts: it refuses
  to start while a tool is down (`host not found in upstream`) and keeps an old IP after a tool
  is recreated, giving 502 until the workbench restarts. `resolver 127.0.0.11` plus a variable
  in `proxy_pass` makes it resolve per request; verified by recreating only task-notif and
  probing through the proxy. With a variable, `proxy_redirect default` is not allowed, so the
  absolute rewrite is spelled out.
- **2026-09-28.** The engine probe runs from a page on one origin to a server on 127.0.0.1.
  Over the documented SSH tunnel both are localhost and browsers allow it. If the workbench is
  ever opened from a non-localhost address, Chrome's local-network access rules require the
  engine's preflight to answer `Access-Control-Allow-Private-Network: true` (it does) and may
  still prompt; Firefox and Safari differ. Keep using the tunnel.
- **2026-09-28.** The standalone `tools/workbench/docker-compose.yml` runs in its own Compose
  project, whose network cannot see the stack's containers. It now joins the stack's
  `snippets_default` network (the stack sets `name: snippets` so that name is stable).
