# Workbench: architecture

```
browser (via ssh -L 4300)
   │
   ▼
nginx :80 in the snippets-workbench container
   ├── /               index.html (the tabs)
   ├── /task-notif/*   → http://task-notif:4310/*    (prefix stripped)
   ├── /keep-alive/*   → http://keep-alive:4311/*    (prefix stripped)
   └── /healthz        200, for Docker's healthcheck

browser ── directly ──▶ http://127.0.0.1:4320   the emulation engine on the viewer's Mac
```

**Prefix stripping.** `proxy_pass http://task-notif:4310/;` with the trailing slash makes
nginx replace the matched `/task-notif/` with `/`. The tool serves itself at `/` as it
always did. For that to work in a browser the tool's HTML must reference its assets and API
relatively (`./assets/x.js`, `api/config`), which Vite does with `base: './'`.

**The engine tab.** `index.html` polls `http://127.0.0.1:4320/status` every two seconds
with a CORS request. The engine answers with `Access-Control-Allow-Origin: *`, so the
workbench page, served from the VM, is allowed to read the reply. When the request fails the
tab shows a notice with the exact command to start the engine, and the header dot goes red.
When it succeeds the console page is loaded into the tab's iframe.

**Lazy frames.** A tool's iframe gets its `src` the first time its tab is opened, so
opening the workbench does not fetch three dashboards at once.

**Auth.** nginx passes the `Authorization` header through, so a tool that sets
`DASHBOARD_PASSWORD` still prompts for it. All frames share the workbench origin, so the
browser asks once per tool, not on every load.
