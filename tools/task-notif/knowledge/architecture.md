# task-notif: architecture

A single Node process (TypeScript, Express) holds the API, the scheduler, the Slack Socket
Mode connection and the undo sweeper, and serves a React dashboard. State is one JSON file.
Paths below are relative to `tools/task-notif/`.

## The pipeline

```
Teamwork API ──> teamwork client ──> discovery / identity ──> rules ──> reminder message ──> Slack DM
                      │                                          │
                      └──> digest / report ──> stand-up summary ─┘
Slack search API (per-person user token) ──> mentions ──> both messages
```

**1. Teamwork client** (`src/teamwork/client.ts`). Every request goes through one queue with a
400 ms gap, about 150 requests a minute, and 429s and 5xxs back off exponentially honouring
`Retry-After`. It reads the v3 API: one workspace-wide sweep of recent comments, one sweep
of recently updated tasks, the people list, board columns per workflow, the activity feed,
and time logged. Two writes are v1 endpoints (comment, complete) and are never retried,
because a write that may already have landed must not be repeated.

**2. Discovery and identity** (`src/teamwork/discovery.ts`, `src/teamwork/identity.ts`).
Teamwork does not expose the `@handle` anywhere in its API, so `discovery.ts` finds handles
by scanning real comments for markdown mentions like `[@ArjunR](/app/people/400002)`, which
tie a handle to a user id. The dashboard's "Add person" search uses this. `identity.ts`
turns a recipient into an `Identity` (user id plus handles) and decides how a comment
refers to them: `directed`, `cc` (inside a courtesy-copy list) or `none`.
`src/teamwork/stage-range.ts` maps board column names to the configured range, leniently.
`src/teamwork/people-directory.ts` matches a Slack user to a Teamwork person (email first,
then a unique full name) so a reply written in Slack can tag people in Teamwork.

**3. Scan** (`src/pipeline.ts`). `workspacesByToken` groups recipients by the Teamwork token
they are read with (their own if set, otherwise the shared one) and runs one sweep per
token, all in parallel. `evaluateRecipient` builds the candidate set (assigned tasks plus
tasks that mention them), drops tasks marked Done unless a newer comment revived them, walks
a subtask up to its parent to find a board column, applies the board range, then runs the
rules in priority order and keeps the first hit. `runScan` handles `mirrorOf` recipients by
copying another person's result.

**4. Rules** (`src/rules/`). One file per rule (`awaiting-response.ts`, `action-requested.ts`,
`overdue.ts`), a registry in `index.ts` (`ALL_RULES`, `activeRules`), shared helpers in
`shared.ts` (`lastUnansweredMention`, `looksLikeRequest`, `isAssignedToMe`) and the `Rule`
contract in `types.ts`. Rules can be switched off from the dashboard.

**5. Digest and report** (`src/digest.ts`, `src/report.ts`). `buildDigest` covers a window in
the recipient's timezone: their comments (with pull-request links extracted and `dev done` /
`blocker` hints by keyword), time logged (the surest trace of work), the activity feed
(completions and other edits, with comment activity dropped because the comments section
already has it), mentions answered and still open, work assigned today, and the Slack
sections. `standupWindow` makes Monday's window span the weekend. `buildRangeReport` reuses
the digest over any past date range for the dashboard's report, from Teamwork only.

**6. Stand-up summary** (`src/llm/`). `standup.ts` builds a prompt from the digest's facts and
asks Gemini (`gemini.ts`, streaming endpoint, key in the query string) when
`standupSummaryEnabled` is on. When it is off, or the call fails, `factual-summary.ts`
assembles the same summary locally from counted lists, with task names as links. Tasks whose
name contains "scrum" are left out of "worked on".

**7. Slack mentions** (`src/slack/mentions.ts`). `SlackMentionSearch` uses the person's own
`xoxp-` user token, because `search.messages` rejects bot tokens and a user token sees only
that person's Slack. Results are re-filtered on the literal `<@UID>` token because search is
fuzzy, broadcasts tagging more than `slackBroadcastThreshold` people are dropped, cc-only
mentions are flagged, and each thread is read to decide answered or not: in a DM any later
message from them counts, in a channel only a thread reply or an emoji reaction counts.

**8. Messages** (`src/slack/message.ts`, `src/slack/digest-message.ts`, `src/slack/paginate.ts`).
`renderReminder` and `renderDigest` build Block Kit. Nothing is trimmed to fit: a message
past Slack's fifty-block ceiling is split by `paginate` into several messages sent in order.
Long summaries are split into sections under the 3,000-character limit
(`summarySections`). Attachments are used only for the coloured left bar.

**9. Delivery** (`src/deliver.ts`). `runAndDeliver(config, teamworkToken, slackToken, job,
trigger)` runs the right pipeline for `reminder`, `digest` or `weekly`, DMs each enabled
recipient with `chat.postMessage` (a user id as the channel opens the DM, so no `im:write`
scope), and records the run. One recipient failing does not stop the others. A failure
during the scan is recorded too. `manualSendOnlyTo` narrows every manual send to one person
so testing never reaches colleagues.

## The scheduler (`src/scheduler/index.ts`)

`nextFireTime` finds the next enabled weekday at the job's local time in the configured
timezone, using Luxon. Timers are clamped to the 24.8-day `setTimeout` ceiling and re-armed.

**Catch-up.** On start, and every 15 minutes after, `shouldCatchUp` checks each job: slot
passed, still inside `catchUpGraceMinutes` (5 hours by default), and no successful run for
it yet. If so it fires, marked late. Any successful send satisfies the slot, including a
manual one, so a restart never sends twice. `SUPPRESS_CATCHUP=1` turns this off.

**Retries.** A failed run retries at 1, 3, 10 and 30 minutes, then gives up until the next
slot. A run that exceeds 12 minutes is treated as wedged and fails. A run already in flight
is never started a second time.

## Slack buttons (`src/slack/socket.ts`, `src/slack/reply.ts`, `src/slack/undo-sweeper.ts`)

`SlackSocket` opens a Socket Mode WebSocket with the `xapp-` App-Level Token. Slack pushes
button clicks down it, so nothing has to be reachable from the internet. It acknowledges
every envelope before doing the work, reconnects with exponential backoff, and retires the
old socket before opening a new one (Slack rejects extras with `too_many_websockets`).

`routeAction` maps a click to one of: `dismiss`, `undo`, `reply`, `complete`, `due`.

- **Dismiss** records the item in the store with the moment it was pressed, rewrites the row
  into a "Done" note with an Undo button, and keeps the removed blocks so Undo can restore
  them. Task keys are `task:<id>`, Slack keys are `channelId:threadTs`.
- **Undo** inside `undoWindowMinutes` (15 by default) removes the dismissal and puts the row
  back. The dashboard's "Marked done" list can undo at any time.
- **Reply** opens a placeholder modal at once (Slack drops a `trigger_id` after three
  seconds), then fills it with the task's last comments. On submit it posts the comment as
  that person, using their own Teamwork token, tagging people by real Teamwork mention
  markup (`src/slack/rich-text.ts`), optionally completes the task, and ticks the row off.
- **Complete** and **due** call Teamwork directly and rewrite the row to say what happened.

`UndoSweeper` runs once a minute and strips the Undo button from any message whose window
has closed, using `chat.update`, which needs every block, so the store keeps the whole
message while an undo is open. Expiry lives in the store, not in a timer, so it survives
restarts.

## State (`src/config/store.ts`, `src/config/schema.ts`)

One file, `data/store.json` (`DATA_DIR` defaults to `./data`, `/app/data` in Docker). Written
atomically (temp file, then rename) with mode `0600` because it can hold Slack user tokens.
It holds `config` (validated by the zod `ConfigSchema`), `dismissals` (capped at 500) and
`runs` (newest first, capped at 50). A corrupt file falls back to defaults and is left in
place. `assertDataDirWritable` runs at start-up and exits with a clear message if the volume
is not writable. Tokens set in the environment win over tokens in the store, and the
dashboard refuses to overwrite them.

## The server and dashboard

`src/server/index.ts` starts Express on `PORT` (4310). `/healthz` is declared before the
optional Basic auth (`src/server/auth.ts`, `DASHBOARD_PASSWORD`, constant-time compare) so a
platform health probe never gets a 401. The API is mounted at `/api` from
`src/server/routes.ts`. In production it serves `dashboard/dist` at `/`. `EADDRINUSE` prints
the `lsof` command to find the other copy.

The dashboard (`dashboard/`, Vite + React 18) has sections for Connections, Schedule,
Recipients, Slack mentions, Rules, Report a date range, Marked done, Recent runs and
Preview. In development `npm --prefix dashboard run dev` serves it on 5310 and proxies
`/api` to 4310.

Command-line tools in `src/cli/`: `probe` (dump live API shapes), `dry-run` (preview the
reminder), `digest-preview`, `send-now`.

Two things sit in this tree today and are moving out: `src/keeper/` with the `/api/keeper/*`
routes and the dashboard's Keep Alive panel (becoming `tools/keep-alive`), and
`src/server/bridge-controller.ts` with `engine/mac_engine.py` (becoming
`tools/emulation-engine`).

## Deploy

**Docker on the always-on VM (the normal way).** `Dockerfile` is two stages on
`node:24-alpine`: build (compiles server and dashboard) and runtime (production
dependencies only, `su-exec` installed). `docker-entrypoint.sh` starts as root, `chown`s the
mounted `/app/data` to `node`, then `exec su-exec node`. The `HEALTHCHECK` fetches
`/healthz` every 60 seconds. `docker-compose.yml` binds `127.0.0.1:4310:4310`, mounts
`./data:/app/data`, sets `TZ: UTC` and reads `.env`. `scripts/deploy.sh user@host` rsyncs
the folder to `/opt/task-notif` (excluding secrets, build output and state), copies `.env`
separately with mode `0600`, runs `docker compose up -d --build`, and waits for the container
to report healthy. `scripts/provision-host.sh` prepares a fresh Ubuntu host: Docker from
Docker's own apt repo with the right architecture, rsync, and 2 GB of swap.

**launchd on a Mac (fallback).** `scripts/install-launchd.sh` builds, writes a plist under
`~/Library/LaunchAgents/com.tasknotif.agent.plist` with logs in `~/Library/Logs/task-notif/`,
loads it, and checks it is actually running. Only fires while the Mac is awake and logged
in.

**Render.** `render.yaml` builds from the Dockerfile with `dockerContext: ./tools/task-notif`.
It needs a paid always-on plan (free instances sleep and the scheduler is the process), a
persistent disk at `/app/data`, and `DASHBOARD_PASSWORD` because the URL is public. Secrets
are set in the Render dashboard (`sync: false`).
