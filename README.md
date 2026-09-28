# snippets

The team's repo of small internal tools. One folder per tool under `tools/`. Each tool is
self-contained: its own README, its own `knowledge/` folder, its own dependencies, tests,
state and deploy story. The only things shared are this repo and the conventions in
`knowledge/`.

## Tools

| Tool | What it does | Where it runs | Docs |
|---|---|---|---|
| `task-notif` | Reads Teamwork and sends each person two Slack DMs a day: a morning reminder of what needs them, and an evening digest of what they did. TypeScript, Express and React. | Docker container on the always-on VM, port 4310 | [README](tools/task-notif/README.md) · [knowledge](tools/task-notif/knowledge/) |
| `keep-alive` | Pings dev and QA instances so DevOps' 20-minute idle stop does not kill them while someone is working, and presses the maintenance page's Start button if one goes down anyway. Its own Express service with a small React page. | Docker container on the same VM, port 4311 | [README](tools/keep-alive/README.md) · [knowledge](tools/keep-alive/knowledge/) |
| `emulation-engine` | Core Emulation Engine Control Console. Python plus Quartz. Generates mouse, keyboard and scroll events on the Mac it runs on, and serves its own console page. | Your own Mac, HTTP on `127.0.0.1:4320`. Run `./install.sh` once, then `./run.sh`. | [README](tools/emulation-engine/README.md) · [knowledge](tools/emulation-engine/knowledge/) |
| `workbench` | One dashboard page with a tab per tool. The Task Notif and Keep Alive tabs go through the nginx proxy to the VM containers. The Emulation Engine tab talks to the engine running on the viewer's own Mac. | nginx on the VM, port 4300 | [README](tools/workbench/README.md) · [knowledge](tools/workbench/knowledge/) |

The VM tools are bound to loopback on the VM. Reach them through an SSH tunnel:

```bash
./deploy/deploy.sh user@host              # sync every VM tool and restart the stack
ssh -N -L 4300:127.0.0.1:4300 user@host   # then open http://localhost:4300
```

Port numbers, the tunnel pattern and the shared Docker conventions are in
[knowledge/conventions.md](knowledge/conventions.md).

## Adding a new tool

1. Copy `tools/_template` to `tools/<tool-name>`. Folder names are kebab-case.
2. Fill in `README.md` and the four files under `knowledge/`. Each template file has
   comments saying what to write.
3. Meet the tool contract in [CONTRIBUTING.md](CONTRIBUTING.md): own dependency
   manifest, own tests, own state, own deploy story. If it runs on the VM, answer
   `/healthz` and take the next free port from `knowledge/conventions.md`.
4. Add a row to the table above and a row to the ports table in
   `knowledge/conventions.md`.
5. Branch, commit, open a pull request. Details in CONTRIBUTING.md.

## Layout

```
snippets/
  README.md                  this file
  CONTRIBUTING.md            the tool contract and how changes land
  deploy/                    the VM stack: docker-compose.yml for every VM tool, deploy.sh
  knowledge/                 repo-wide conventions (ports, naming, Docker patterns)
    README.md
    conventions.md
  tools/
    _template/               copy this to start a new tool
      README.md
      knowledge/{intro,architecture,lessons,cheatsheet}.md
    task-notif/              Teamwork -> Slack reminders and digests (VM, :4310)
      README.md
      knowledge/
      src/  dashboard/  scripts/  Dockerfile  docker-compose.yml
    keep-alive/              keeps dev/QA instances awake (VM, :4311)
      README.md
      knowledge/
    emulation-engine/        Mac input emulation engine and console (local Mac, :4320)
      README.md
      knowledge/
    workbench/               one page, one tab per tool (VM, nginx, :4300)
      README.md
      knowledge/
```
