# Workbench

One page with a tab for every tool, so nobody has to remember which port each one lives on.

| Tab | What it shows | Where it runs |
|---|---|---|
| Task Notif | The Task Notif dashboard | the VM, through the proxy at `/task-notif/` |
| Keep Alive | The Keep Alive page | the VM, through the proxy at `/keep-alive/` |
| Emulation Engine | The engine's own console | **your Mac**, at `127.0.0.1:4320` |

The workbench is a single static page and an nginx reverse proxy. The tools do not know it
exists: each one still serves itself at `/` on its own port, and nginx strips the `/<tool>/`
prefix before forwarding. That is why every tool's page uses relative asset and API paths.

The Emulation Engine tab is different. The engine drives the mouse and keyboard of the machine
it runs on, so it can never live on the server. The tab talks to the engine on the viewer's own
Mac and shows how to start it when it is not running.

## Running it

On the VM the workbench is part of the stack in `deploy/docker-compose.yml` at the repo root:

```bash
./deploy/deploy.sh user@host                  # from your machine: sync, build, restart, wait
ssh -N -L 4300:127.0.0.1:4300 user@host       # then open http://localhost:4300
```

The port is bound to loopback on the VM on purpose. The tools behind it have at most a
password, so it must not be reachable from the internet.

To run just the workbench container by itself (for example to test an nginx change) use
`docker compose up` in this folder; it expects containers named `task-notif` and `keep-alive`
on the same network.

## Files

- `index.html`: the tabs, the engine check, nothing else. No build step.
- `nginx.conf`: the proxy. One `location` per VM tool.
- `Dockerfile`: nginx plus those two files.

## Adding a tool to the workbench

1. If the tool runs on the VM, add a service to `deploy/docker-compose.yml`, a `location
   /<tool>/ { proxy_pass http://<tool>:<port>/; ... }` block to `nginx.conf`, and make sure
   the tool's page uses relative paths (`./assets/...`, `api/...`) so it works under a prefix.
2. Add a tab button and a panel with an iframe in `index.html`.
3. If the tool runs on people's own machines instead, copy the engine tab's pattern: probe it
   on `127.0.0.1` and show instructions when it is not there.
