# Workbench: cheatsheet

| Thing | Value |
|---|---|
| Port on the VM | 4300 (loopback only) |
| Open it | `ssh -N -L 4300:127.0.0.1:4300 user@host` then http://localhost:4300 |
| Deploy the whole VM stack | `./deploy/deploy.sh user@host` |
| Restart on the VM | `cd /opt/snippets/deploy && docker compose up -d --build` |
| Logs | `docker compose logs -f workbench` (same folder) |
| Health | `curl http://127.0.0.1:4300/healthz` on the VM |
| Test an nginx edit | `docker compose up --build` in `tools/workbench` next to running tool containers |
| Engine probe URL | `http://127.0.0.1:4320/status` on the viewer's Mac |
