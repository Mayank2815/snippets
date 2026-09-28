# Plan: restructure `task-notif` into the multi-tool `snippets` repo

Status: DRAFT for lead review. No files moved yet.
Date: 2026-09-28

## What the lead asked for

"Make a repo called snippets (maybe just rename task_notif, it is already on GitHub).
In it: one folder per tool, plus knowledge for each. Going forward every tool we build
goes there."

Tools that exist today:

| Tool | What it is | Where it lives now | Committed? |
|---|---|---|---|
| Task Notif | Teamwork -> Slack morning reminder + evening digest. TypeScript, Express, React dashboard, Docker on an always-on VM. | `src/`, `dashboard/`, `Dockerfile`, `scripts/` | Yes (13 commits) |
| Keep Alive | Pings dev/QA instances so DevOps' 20-minute idle stop does not kill them; presses Start if one goes down. | `src/keeper/`, `/api/keeper` routes in `src/server/routes.ts`, `keepAlive` block in `src/config/schema.ts`, `dashboard/src/components/KeepAlivePanel.tsx`, `src/__tests__/keeper.test.ts` | No, uncommitted |
| Core Emulation Engine Control Console | Mac-only Python engine (Quartz input events, HTTP on :4320) started/stopped from the dashboard through an Express bridge. | `engine/mac_engine.py`, `engine/test_metrics.py`, `src/server/bridge-controller.ts`, `dashboard/src/components/AutomationPanel.tsx` | No, uncommitted |

The second and third tools are currently welded into Task Notif's server process,
config store (`data/store.json`) and dashboard. That coupling is the main thing the
restructure has to undo.

## Target layout

```
snippets/
├── README.md                  # what this repo is, tool index, how to add a tool
├── CONTRIBUTING.md            # the "tool contract" (see below)
├── knowledge/                 # cross-cutting: conventions, shared gotchas, env setup
│   ├── README.md
│   └── conventions.md
├── tools/
│   ├── _template/             # copy this to start a new tool
│   │   ├── README.md
│   │   └── knowledge/{intro.md,architecture.md,lessons.md,cheatsheet.md}
│   ├── task-notif/            # moved as-is: src/, dashboard/, Dockerfile, scripts/, package.json
│   │   ├── README.md          # today's README, unchanged
│   │   └── knowledge/
│   ├── keep-alive/            # extracted from task-notif
│   │   ├── README.md
│   │   ├── knowledge/
│   │   ├── src/               # keeper/index.ts, its own routes, its own store
│   │   ├── ui/                # KeepAlivePanel + a tiny page shell
│   │   └── package.json
│   └── emulation-engine/      # name TBC (see Q4)
│       ├── README.md
│       ├── knowledge/
│       ├── engine/mac_engine.py, test_metrics.py
│       ├── console/           # bridge-controller.ts + AutomationPanel.tsx
│       └── package.json / requirements.txt
├── package.json               # npm workspaces: tools/task-notif, tools/keep-alive, tools/emulation-engine/console
└── .github/                   # (later) one CI job per tool, path-filtered
```

Per-tool `knowledge/` mirrors the org's product-workspace convention
(`docs/dev/<product>/`) but keeps the lead's word "knowledge". Minimum four files:
`intro.md` (what/why), `architecture.md`, `lessons.md` (gotchas, dated),
`cheatsheet.md` (commands, endpoints, where creds live).

## The tool contract (goes in CONTRIBUTING.md)

Every folder under `tools/` must have:
1. `README.md` with purpose, how to run, how to deploy, env vars.
2. `knowledge/` with the four files above.
3. Its own dependency manifest (`package.json` or `requirements.txt`) and its own tests.
4. Its own state. No tool writes into another tool's data file.
5. Its own deploy story (Docker, launchd, or "run it on your Mac"). A tool that needs a
   different host than another tool must not be in the same process.

## The decision that changes the amount of work: one process or three?

**Option A: keep one host process, split by folder only.**
Task Notif's Express server keeps mounting keep-alive and the engine bridge; folders
just move. Cheap (a day). But ownership is cosmetic: the engine is Mac-only and cannot
run inside the Docker container on the VM at all, so the "console" in the deployed
dashboard is permanently OFFLINE there. Keep-alive config stays inside Task Notif's
`store.json`.

**Option B: three fully independent tools (recommended).**
- `task-notif` goes back to being purely Teamwork -> Slack. Remove the keeper routes,
  the `keepAlive` schema block, the bridge mount and the two panels.
- `keep-alive` gets its own small Express server (port 4311), its own `data/keep-alive.json`,
  its own single-page UI (the existing panel), its own Dockerfile and compose service.
  It is a background pinger, so it ships to the same VM as Task Notif, as a second
  container.
- `emulation-engine` becomes a Mac-side tool: the Python engine plus the bridge/console
  served on the Mac (port 4320 already exists). Its README says plainly "runs on the
  machine whose input it drives; needs Accessibility permission".
Cost: roughly two to three days. Benefit: each folder truly is one tool, one owner, one
deploy, one knowledge base, which is what the lead described.

**Option C: B, plus a thin root "workbench" page** that just links to each tool's UI.
Only worth it if people miss having one dashboard. Can be added later; nothing in B
blocks it.

Recommendation: **B**. Do not spend on C until someone asks.

## Sequence

1. **Commit what exists first.** ~1,900 lines of keep-alive and engine work are
   uncommitted. Put them on a branch and land them as-is, so the restructure commit is
   pure moves and history stays readable.
2. **Rename the GitHub repo** `task-notif` -> `snippets` (Settings -> General -> Rename).
   GitHub redirects the old URL and old clones keep working; update `origin` locally.
   (See Q1.)
3. **Move Task Notif** into `tools/task-notif/` with `git mv` in one commit. Fix the
   few path assumptions: `Dockerfile` build context, `scripts/deploy.sh` remote dir,
   `render.yaml` `dockerfilePath`, `install-launchd.sh` working dir.
4. **Extract Keep Alive** into `tools/keep-alive/` (Option B). Migrate the `keepAlive`
   block out of `data/store.json` with a one-off read on first start.
5. **Extract the engine** into `tools/emulation-engine/`. Uncomment-or-delete decision
   on `mac_engine.py`: today the file is 207 commented lines followed by 199 live lines,
   apparently an old copy left above the current one. Keep only the live copy.
6. **Write the knowledge folders.** Task Notif's README already holds most of its
   knowledge; split it into the four files rather than rewriting. Keep Alive's
   `keeper/index.ts` header comment and the 23 September lessons go to `lessons.md`.
7. **Root README + CONTRIBUTING + `_template/`.**
8. **Verify:** `npm test` per workspace, Docker build for task-notif and keep-alive,
   deploy both containers to the VM, engine started from a Mac. Screenshots of each UI.
9. **Later, not now:** GitHub Actions, one job per tool with `paths:` filters.

## Risks

- The deploy script rsyncs the repo root to `/opt/task-notif`. After the move it must
  rsync `tools/task-notif` only, or the VM gets the whole monorepo. Easy to miss.
- `data/` is gitignored and lives on the VM as a volume. Moving the tool must not
  change the volume mount path, or config and run history vanish on first redeploy.
- Renaming the repo breaks nothing on GitHub, but any Render service or webhook pointed
  at the old name should be re-checked.

## Open questions for the lead

Q1. Rename `task-notif` -> `snippets` in place (keeps stars, issues, history, redirect),
    or create a fresh `snippets` repo and archive `task-notif`? Recommend rename.
Q2. One process (Option A) or independent tools (Option B)? Recommend B.
Q3. Should Keep Alive be deployed next to Task Notif on the same VM? (B assumes yes.)
Q4. Folder name for the engine tool: `emulation-engine`, `activity-engine`, or the
    full `core-emulation-engine-console`? Recommend `emulation-engine`.
Q5. Is `mac_engine.py` meant to be committed with the commented-out duplicate at the
    top, or is that scratch to be dropped?
Q6. Is there a fourth tool already in flight anywhere that should be planned for now?

## Lead's answers (2026-09-28)

- Q1 Rename in place. Q2 Option B, plus one dashboard so every feature is reachable from
  a single UI. Q3 Keep Alive on the same VM. Q4 `emulation-engine`. Q5 drop the
  commented-out copy. Q6 a fourth tool is in ideation only.
- Emulation engine must be easy to run on other people's Macs (Rohit sir first).
- The uncommitted work lands as TWO commits: keep-alive, then emulation-engine.

## Revised design for the single dashboard ("workbench")

```
tools/workbench/            # the one UI, composes each tool's panel
  src/App.tsx               # tabs: Task Notif | Keep Alive | Emulation Engine
  nginx.conf                # /task-notif/* -> task-notif:4310, /keep-alive/* -> keep-alive:4311
```
- Each tool owns its panel code under `tools/<tool>/ui/` and the workbench imports it, so
  tool ownership stays with the tool folder; the workbench only assembles.
- Task Notif and Keep Alive tabs call their VM backends through the nginx proxy.
- The Emulation Engine tab calls `http://127.0.0.1:4320` on the viewer's own Mac (the
  engine must drive that machine's input), so the engine's HTTP server gains CORS and the
  tab shows "engine not running on this Mac, run tools/emulation-engine/run.sh" when it
  is down.
- Same `DASHBOARD_PASSWORD` basic auth on both backends; nginx passes it through.

## Making the engine runnable on another Mac

`tools/emulation-engine/` ships:
- `install.sh`: creates `.venv`, installs `pyobjc-framework-Quartz`, checks Accessibility
  permission and tells the user exactly which System Settings toggle to flip.
- `run.sh`: starts the engine on :4320 and opens the console page.
- `README.md`: three-line quick start, then a "sharing" section.
- `make bundle`: zips the folder (no git needed) for anyone without repo access.

## Commit sequence

1. `feat(keep-alive): ...` current uncommitted keep-alive code, as-is
2. `feat(emulation-engine): ...` current uncommitted engine + bridge + panel, as-is
3. Rename GitHub repo, update origin
4. `refactor(repo): move task-notif into tools/task-notif` (pure git mv)
5. `refactor(keep-alive): extract into tools/keep-alive as its own service`
6. `refactor(emulation-engine): extract into tools/emulation-engine, drop dead copy`
7. `feat(workbench): single dashboard composing all tool panels`
8. `docs: root README, CONTRIBUTING, tools/_template, knowledge folders`

## Outcome (2026-09-28, end of session)

Done, in this order, all via rebase-merged PRs on github.com/Mayank2815/snippets:

| PR | What |
|---|---|
| #1 | Landed the uncommitted work as three commits: task-notif (hours logged pill), keep-alive, emulation-engine |
| #2 | `git mv` of the whole app into `tools/task-notif` |
| #3 | Root README, CONTRIBUTING, `tools/_template`, `tools/task-notif/knowledge` |
| #4 | `tools/emulation-engine`: engine serves its own console, CORS, install.sh, run.sh, Makefile bundle, knowledge |
| #5 | `tools/keep-alive`: own Express service, UI, store, Docker, tests, knowledge; removed from task-notif |
| #7 | keep-alive review fixes (hours validation crash, legacy import guard, SSRF hardening, JSON 404s) |
| #6 | `tools/workbench` (nginx + tab page) and `deploy/` (whole VM stack, migration from /opt/task-notif) |
The final design differs from the first draft in two ways: the workbench uses one iframe per
tool rather than importing panels (true independence, and the engine tab naturally targets
the viewer's Mac), and the legacy Keep Alive import reads a snapshot taken by deploy.sh
instead of a live read-only mount (the live read was a race).

| #8 | This plan and the request log, committed with the project |
| #9 | emulation-engine review fixes (double-loop race on Stop then Start, origin allow-list + control header on POST, SIGTERM shutdown) |

Not done in this session: the first real deploy to the VM with `./deploy/deploy.sh user@host`
(host not known to the agent). The script migrates an existing `/opt/task-notif` install on
its first run.
