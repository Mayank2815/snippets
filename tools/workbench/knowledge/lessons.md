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
