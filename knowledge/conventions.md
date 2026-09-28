# Conventions

Shared rules for every tool in this repo.

## Ports

Each tool owns one port. VM tools bind to loopback on the VM and are reached through an
SSH tunnel or through the workbench proxy. Take the next free number when adding a tool,
and record it here in the same pull request.

| Port | Tool | Host | Notes |
|---|---|---|---|
| 4300 | `workbench` | VM (nginx) | One page, one tab per tool. Proxies to 4310 and 4311. |
| 4310 | `task-notif` | VM (Docker) | Express API, scheduler and dashboard. |
| 4311 | `keep-alive` | VM (Docker) | Express API and its page. |
| 4320 | `emulation-engine` | The viewer's own Mac | Python HTTP server on `127.0.0.1:4320`. Never on the VM. |

Development servers use the same number plus 1000 where a tool has one
(`task-notif`'s Vite dev server is 5310 and proxies `/api` to 4310).

## Naming

- Tool folders are kebab-case: `task-notif`, `keep-alive`, `emulation-engine`.
- The Docker image and container carry the folder name: `image: task-notif:latest`,
  `container_name: task-notif`.
- The commit scope is the folder name.
- Environment variables are `UPPER_SNAKE_CASE`. Per-person variables append the person's
  id with non-alphanumerics turned into underscores: recipient `alex-kim` becomes
  `SLACK_USER_TOKEN_ALEX_KIM`.

## Where things live on the VM

| Path | What |
|---|---|
| `/opt/<tool>/` | The tool's synced checkout. `deploy.sh` rsyncs the folder here. |
| `/opt/<tool>/.env` | Its secrets. Copied separately by the deploy script and never deleted by the sync, so a bad deploy cannot wipe credentials. Mode `0600`. |
| `/opt/<tool>/data/` | Its state. Mounted into the container at `/app/data`. Survives redeploys because it is a bind mount, not part of the image. |

One tool never reads or writes another tool's `/opt/<tool>/` directory.

## Reaching a VM tool: the SSH tunnel

The VM tools are bound to `127.0.0.1` on the VM on purpose. Their dashboards have little
or no authentication, so they must not be reachable from the internet. Open a tunnel and
use `localhost`:

```bash
ssh -N -L <port>:127.0.0.1:<port> user@host
# for example
ssh -N -L 4300:127.0.0.1:4300 user@host    # workbench, all tools in one page
ssh -N -L 4310:127.0.0.1:4310 user@host    # task-notif on its own
```

`-N` means "no remote command, just hold the tunnel". Leave it running in a terminal
and open `http://localhost:<port>` in the browser.

If a dashboard is ever exposed on a public URL (Render, a cloud load balancer), put a
password in front of it. `task-notif` reads `DASHBOARD_PASSWORD` for exactly this.

## Docker patterns shared by the VM tools

Every VM tool follows the same shape so that deploy scripts, healthchecks and the
workbench proxy can treat them alike.

**Healthcheck on `/healthz`.** The app answers `GET /healthz` with `200` and a small JSON
body. It is declared before any authentication middleware, because the platform's health
probe sends no credentials, and a `401` would make the host think the service is dead and
restart it forever. The Dockerfile declares it:

```dockerfile
HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||4310)+'/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
```

The deploy script waits for `docker inspect` to report `healthy` before it says "done".

**Entrypoint chowns the volume, then drops to `node`.** A bind-mounted volume arrives with
the host's ownership, which overrides whatever the image `chown`ed at build time. So a
container running as a non-root user cannot write its own data directory. The pattern
(see `tools/task-notif/docker-entrypoint.sh`) is: start as root, `chown -R node:node
"$DATA_DIR"`, then `exec su-exec node "$@"`. The app itself never runs as root. There is no
`USER` directive in the Dockerfile for this reason.

**Loopback port binding in compose.**

```yaml
ports:
  - "127.0.0.1:4310:4310"
```

**Volume at `/app/data`.** `DATA_DIR=/app/data` in the image; `./data:/app/data` in compose.

**Multi-stage build.** A build stage compiles the server and any front end; the runtime
stage installs production dependencies only, with no toolchain.

**Container clock is UTC.** `TZ: UTC` in compose. Anything that needs a local time
computes it from a configured timezone rather than from the container clock.
