#!/usr/bin/env bash
# Ships every VM-hosted tool to the always-on host and restarts the whole stack there.
#   ./deploy/deploy.sh user@host [remote-dir]
# Each tool's .env is copied separately and never deleted by the sync, so a bad deploy
# cannot wipe credentials. Each tool's data/ is a volume on the host and is never synced.
set -euo pipefail

TARGET="${1:-}"
REMOTE_DIR="${2:-/opt/snippets}"
# Where the old single-tool deploy script put Task Notif. Migrated once, see below.
OLD_TASK_NOTIF_DIR="/opt/task-notif"
if [ -z "$TARGET" ]; then
  echo "usage: ./deploy/deploy.sh user@host [remote-dir]" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Only the tools that run on the VM. The emulation engine runs on people's own Macs.
VM_TOOLS=(task-notif keep-alive workbench)

echo "==> Preparing $TARGET:$REMOTE_DIR"
ssh "$TARGET" "mkdir -p '$REMOTE_DIR/deploy' $(printf "'$REMOTE_DIR/tools/%s/data' " "${VM_TOOLS[@]}")"

# Task Notif used to be deployed on its own to /opt/task-notif by tools/task-notif/scripts/
# deploy.sh, as a container named task-notif. The stack uses the same container name and a
# new data path, so the first stack deploy on such a host must stop the old container and
# carry its store and .env across, or the new one boots empty and the name collides.
echo "==> Migrating a single-tool Task Notif deploy, if there is one"
ssh "$TARGET" "if [ -f '$OLD_TASK_NOTIF_DIR/data/store.json' ] && [ ! -f '$REMOTE_DIR/tools/task-notif/data/store.json' ]; then
  echo '    found $OLD_TASK_NOTIF_DIR — stopping the old container and copying its data'
  if [ -f '$OLD_TASK_NOTIF_DIR/docker-compose.yml' ]; then (cd '$OLD_TASK_NOTIF_DIR' && docker compose down) || true; fi
  docker rm -f task-notif >/dev/null 2>&1 || true
  cp -a '$OLD_TASK_NOTIF_DIR/data/.' '$REMOTE_DIR/tools/task-notif/data/' || sudo -n cp -a '$OLD_TASK_NOTIF_DIR/data/.' '$REMOTE_DIR/tools/task-notif/data/'
  [ -f '$OLD_TASK_NOTIF_DIR/.env' ] && [ ! -f '$REMOTE_DIR/tools/task-notif/.env' ] && cp '$OLD_TASK_NOTIF_DIR/.env' '$REMOTE_DIR/tools/task-notif/.env' || true
  echo '    migrated; $OLD_TASK_NOTIF_DIR is left in place as a backup'
else echo '    nothing to migrate'; fi"

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
  else
    # Compose refuses to start a service whose env_file is missing, so an empty one is
    # created rather than failing the whole stack over a tool that needs no secrets.
    echo "==> No local tools/$tool/.env; making sure an (empty) one exists on the host"
    ssh "$TARGET" "[ -f '$REMOTE_DIR/tools/$tool/.env' ] || (touch '$REMOTE_DIR/tools/$tool/.env' && chmod 600 '$REMOTE_DIR/tools/$tool/.env')"
  fi
done
rsync -az "$ROOT/deploy/docker-compose.yml" "$TARGET:$REMOTE_DIR/deploy/docker-compose.yml"

# Keep Alive once lived inside task-notif and kept its instance list in that tool's
# store.json. The new task-notif drops that block on its first write, so the list is
# copied aside before anything restarts; keep-alive imports it on its first boot only.
# store.json is 0600 and owned by the container's uid 1000; on a host where the SSH user is
# someone else the copy needs sudo, and if that is unavailable the deploy still goes on.
echo "==> Preserving any legacy Keep Alive instances from task-notif's store"
ssh "$TARGET" "if [ -f '$REMOTE_DIR/tools/task-notif/data/store.json' ] && [ ! -f '$REMOTE_DIR/tools/keep-alive/data/keep-alive.json' ]; then
  cp -n '$REMOTE_DIR/tools/task-notif/data/store.json' '$REMOTE_DIR/tools/keep-alive/data/legacy-task-notif-store.json' 2>/dev/null \
    || sudo -n cp -n '$REMOTE_DIR/tools/task-notif/data/store.json' '$REMOTE_DIR/tools/keep-alive/data/legacy-task-notif-store.json' 2>/dev/null \
    || echo '    could not read store.json (permissions); continuing without the snapshot'
  [ -f '$REMOTE_DIR/tools/keep-alive/data/legacy-task-notif-store.json' ] && echo '    snapshot taken' || true
else echo '    nothing to do'; fi"

echo "==> Building and restarting the stack"
ssh "$TARGET" "cd '$REMOTE_DIR/deploy' && docker compose up -d --build"

echo "==> Waiting for health"
# Thirty tries three seconds apart. up -d returns only after the images are built, and the
# first health probe runs right after each container starts, so 90 s is generous.
# docker logs takes the container name; docker compose logs would want the service name.
ssh "$TARGET" "cd '$REMOTE_DIR/deploy' && for c in task-notif keep-alive snippets-workbench; do
  for i in \$(seq 1 30); do
    status=\$(docker inspect --format '{{.State.Health.Status}}' \$c 2>/dev/null || echo starting)
    [ \"\$status\" = healthy ] && echo \"    \$c healthy\" && break
    [ \"\$status\" = unhealthy ] && echo \"    \$c UNHEALTHY\" && docker logs --tail 40 \$c && exit 1
    sleep 3
    [ \$i = 30 ] && echo \"    \$c still not healthy after 90 s\" && docker logs --tail 40 \$c && exit 1
  done
done"

echo "==> Done. Open the workbench with:"
echo "    ssh -N -L 4300:127.0.0.1:4300 $TARGET   then   http://localhost:4300"
