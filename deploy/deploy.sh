#!/usr/bin/env bash
# Ships every VM-hosted tool to the always-on host and restarts the whole stack there.
#   ./deploy/deploy.sh user@host [remote-dir]
# Each tool's .env is copied separately and never deleted by the sync, so a bad deploy
# cannot wipe credentials. Each tool's data/ is a volume on the host and is never synced.
set -euo pipefail

TARGET="${1:-}"
REMOTE_DIR="${2:-/opt/snippets}"
if [ -z "$TARGET" ]; then
  echo "usage: ./deploy/deploy.sh user@host [remote-dir]" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Only the tools that run on the VM. The emulation engine runs on people's own Macs.
VM_TOOLS=(task-notif keep-alive workbench)

echo "==> Preparing $TARGET:$REMOTE_DIR"
ssh "$TARGET" "mkdir -p '$REMOTE_DIR/deploy' $(printf "'$REMOTE_DIR/tools/%s/data' " "${VM_TOOLS[@]}")"

for tool in "${VM_TOOLS[@]}"; do
  echo "==> Syncing tools/$tool (excluding secrets, build output and local state)"
  rsync -az --delete \
    --exclude node_modules --exclude '*/node_modules' \
    --exclude dist --exclude '*/dist' \
    --exclude data --exclude logs --exclude .env --exclude .git \
    "$ROOT/tools/$tool/" "$TARGET:$REMOTE_DIR/tools/$tool/"
  if [ -f "$ROOT/tools/$tool/.env" ]; then
    echo "==> Syncing tools/$tool/.env (0600)"
    scp -q "$ROOT/tools/$tool/.env" "$TARGET:$REMOTE_DIR/tools/$tool/.env"
    ssh "$TARGET" "chmod 600 '$REMOTE_DIR/tools/$tool/.env'"
  fi
done
rsync -az "$ROOT/deploy/docker-compose.yml" "$TARGET:$REMOTE_DIR/deploy/docker-compose.yml"

echo "==> Building and restarting the stack"
ssh "$TARGET" "cd '$REMOTE_DIR/deploy' && docker compose up -d --build"

echo "==> Waiting for health"
# Thirty tries three seconds apart: the Node images take about a minute to build
# on the small VM and each healthcheck has a 15 s start period.
ssh "$TARGET" "cd '$REMOTE_DIR/deploy' && for c in task-notif keep-alive snippets-workbench; do
  for i in \$(seq 1 30); do
    status=\$(docker inspect --format '{{.State.Health.Status}}' \$c 2>/dev/null || echo starting)
    [ \"\$status\" = healthy ] && echo \"    \$c healthy\" && break
    [ \"\$status\" = unhealthy ] && echo \"    \$c UNHEALTHY\" && docker compose logs --tail 40 \$c && exit 1
    sleep 3
    [ \$i = 30 ] && echo \"    \$c still not healthy after 90 s\" && exit 1
  done
done"

echo "==> Done. Open the workbench with:"
echo "    ssh -N -L 4300:127.0.0.1:4300 $TARGET   then   http://localhost:4300"
