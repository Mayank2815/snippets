#!/usr/bin/env bash
# Task Notif is deployed as part of the team VM stack, not on its own. The old single-tool
# deploy put it at /opt/task-notif; deploy/deploy.sh at the repo root migrates that once.
echo "Task Notif is deployed with the whole stack now:" >&2
echo "    $(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/deploy/deploy.sh user@host" >&2
echo "That syncs every VM tool, builds them, restarts the stack and waits for health." >&2
exit 1
