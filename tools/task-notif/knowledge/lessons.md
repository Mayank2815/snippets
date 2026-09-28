# task-notif: lessons

Newest first. Dates are the day the lesson was learned, taken from the code comment or the
commit that fixed it. Paths are relative to `tools/task-notif/`.

## September 2026

- **2026-09-23** Reading everyone through one shared Teamwork token hid whole boards.
  Teamwork shows a token only the projects its owner can see, so a recipient working on a
  DevOps board came back as "nothing recorded" while her own token found her time and
  closed tasks. Each recipient can now give their own token (`TEAMWORK_USER_TOKEN_<ID>` or
  the dashboard), and `teamworkTokenFor` in `src/pipeline.ts` picks it.

- **2026-09-15** An empty list is not "nothing to send". Someone who cleared every item
  the morning before still had a stand-up to read out, and got no message. `nothingToSend`
  in `src/deliver.ts` now sends when there is a summary even if the list is empty.

- **2026-09-14** Sweeping tokens one after another took over three minutes inside a run
  that gives up at twelve. Four tokens swept in parallel take 71 seconds end to end
  (`workspacesByToken`, `src/pipeline.ts`).

- **2026-09-14** Comments alone hid seven of one person's eight tasks, about eight hours,
  from the Monday stand-up. An afternoon of code can leave no comment at all. Time logged
  is now read (`timeLoggedBy` in `src/teamwork/client.ts`) and counts as "worked on".

- **2026-09-14** On the time endpoint, `assignedToUserIds` is the filter that works.
  `userId`, `userIds` and `personId` are ignored without an error and return everybody's
  time. Checked against a client-side count for five people, identical sets. The date
  filter works by whole UTC day, so the exact window is applied afterwards.

- **2026-09-14** Tasks the sweep did not index read as a bare "Task 12345". The sweep
  holds open tasks, so usually these were already closed. Six of one person's seven tasks
  read that way. `src/digest.ts` now asks Teamwork for the few that are missing.

- **2026-09-14** Trimming the message to fit would have hidden 11 of one person's 29
  tasks: four to a twelve-row cap, seven to the fifty-block ceiling. Nothing is trimmed
  now; the rest goes in a second message (`paginate`, `src/slack/paginate.ts`).

- **2026-09-14** A task commented on twice by one reviewer read out as the same task twice
  in the summary. `src/llm/factual-summary.ts` counts tasks, not comments.

- **2026-09-11** The whole weekly message was rejected by Slack because one section held
  3,720 characters, and the recipient got nothing. Slack's limit is 3,000 per section.
  `summarySections` in `src/slack/message.ts` splits between lines so no link is cut.

- **2026-09-11** Trimming to the block ceiling silently dropped whole sections: the Friday
  weekly lost thirteen blocks for one person and six for another. Replaced by pagination.

- **2026-09-11** The reply button did nothing, with no error anywhere. Its click was parsed
  by a rule written for the dismiss buttons and rejected before its own branch was reached.
  `routeAction` in `src/slack/socket.ts` is now separate, exported and tested, because an
  action that falls through routing fails silently.

- **2026-09-11** Slack drops a `trigger_id` after three seconds, and reading a task's
  comments takes longer. The modal simply never appeared on a slow call. A placeholder
  view opens at once and is filled in afterwards (`reply` in `src/slack/socket.ts`).

- **2026-09-11** Reading the task, then the comments, then one request per author took
  4.3 seconds after clicking Reply, nearly all of it waiting on names. The comments
  endpoint sideloads users in the same response: 0.7 seconds (`commentThread`).

- **2026-09-11** A plain "@Name" in a Teamwork comment is only a word that looks like a
  mention. A real one is a link to the person, so replies are posted as HTML
  (`src/slack/rich-text.ts`, `postComment`). Leaving `notify` out notifies nobody.

- **2026-09-11** Posting a comment is never retried. Every other call is a read and safe to
  repeat; a comment that may already have landed must not be posted twice.

- **2026-09-11** Slack honours a `response_url` for thirty minutes. Row rewrites after a
  reply stay inside 29 minutes (`RESPONSE_URL_LIFE_MS`). `chat.update` with channel and
  `ts` never expires, which is why the undo sweeper uses that instead.

- **2026-09-11** The retry ladder (1, 3, 10, 30 minutes) covers about three quarters of an
  hour, but the catch-up grace window stays open for hours. A network outage that
  outlasted the ladder lost the day even though the machine was up. Catch-up is now
  re-checked every 15 minutes (`CATCHUP_POLL_MS`, `src/scheduler/index.ts`).

- **2026-09-11** Only a successful run satisfies a slot. Passing a failed run to
  `shouldCatchUp` silently cancelled the catch-up. Failures are excluded.

- **2026-09-11** A Slack search on 92 people matched 76 by email. Name matching is used
  only when exactly one Teamwork person has that full name, because tagging the wrong one of
  two namesakes would notify a stranger (`matchPerson`, `src/teamwork/people-directory.ts`).

- **2026-09-11** Teamwork appends " *" to many task titles. Left in, it closed the bold in a
  Slack confirmation early. `cleanTaskName` in `src/teamwork/identity.ts` strips it.

- **2026-09-06** launchd agent registered but never ran: `launchctl list` showed `-` and
  exit code `78`, with nothing in the logs. launchd opens the log files itself, before the
  process starts, and cannot open files inside the folders macOS protects (Desktop,
  Documents, Downloads); a checkout there gets its logs stamped with `com.apple.macl`.
  Deleting the files clears it for a while but it comes back. Logs now go to
  `~/Library/Logs/task-notif/` whatever the checkout location (`scripts/install-launchd.sh`).

- **2026-09-06** A launchd label can get wedged: it bootstraps clean and exits 78 at once
  while an identical plist under a new name runs fine. Bumping the label suffix sidesteps
  it.

- **2026-09-06** Monday's stand-up skipped the weekend. `standupWindow` in `src/digest.ts`
  makes Monday cover Friday to Sunday.

- **2026-09-06** Slack's `after:` search filter excludes the date it is given. Step back
  one extra day to include the boundary (`mentionsSince`, `src/slack/mentions.ts`).

- **2026-09-05** The morning run doubled its own work: it searched Slack twice per person
  (pending and yesterday) and fetched the activity feed once per person. The pending window
  already spans yesterday, so one search is filtered two ways, and the feed is fetched once
  per token (`activityPerToken`, `src/deliver.ts`).

- **2026-09-04** `chat.update` replaces rather than patches, so every block must be
  supplied, and a bot token cannot read a DM back. The only copy of the message is the one
  the store keeps while an undo is open (`UndoStateSchema`, `src/config/schema.ts`). Every
  pending undo on one message shares one snapshot, refreshed together, or a second Done
  would leave the first holding a stale copy and undoing it would undo the second.

- **2026-09-04** An Undo button that quietly stopped working would read as broken, because
  pressing Done has no time limit. The button is removed when the window closes rather
  than left there refusing (`src/slack/undo-sweeper.ts`). Expiry lives in the store, not a
  timer, so a restart cannot lose it.

- **2026-09-03** Board column names differ per project in case and hyphenation ("BA
  Signed-off", "BA Signed-Off"), and some boards have no BA column at all. Anchors are
  matched leniently, and a board with no recognisable anchor is left unfiltered. Silently
  hiding a whole project's tasks is the worse failure (`src/teamwork/stage-range.ts`).

- **2026-09-03** The array order the workflow-stages endpoint returns is not the board
  order. `displayOrder` is; one board listed Sprint Backlog before the Backlog columns that
  precede it.

- **2026-09-03** A subtask sits on no board column of its own. Walk up to the parent, at
  most four levels, so "FE: something" under a parent in Ready for QA counts as Ready for QA
  (`stageOf`, `src/pipeline.ts`).

- **2026-09-03** Overdue rows with reply controls cost one more block each. With twelve
  overdue rows and twelve questions that trims four rows, which the budget turns into an
  "and N more" line rather than dropping silently (measured in `block-budget.test.ts`).

- **2026-09-02** Volume ownership. A mounted volume arrives with the host's ownership,
  overriding whatever the image chowned, so a container running as `node` could not write
  its own store. The first build without the fix failed with `EACCES` on the very first
  write. `docker-entrypoint.sh` starts as root only long enough to `chown` the data
  directory, then `su-exec`s to `node`. `assertDataDirWritable` in `src/config/store.ts`
  fails fast with a clear message instead of a silent crash loop.

- **2026-09-02** Mentions are matched on handle, not name. Teamwork writes a mention as
  plain `@Handle` text with no user id, so the handle is the only key. Matching is
  word-boundary exact: `@PriyaS` must not match `@PriyaSha` or `@PriyaSin`, and three
  Priyas share this workspace (`src/teamwork/identity.ts`).

- **2026-09-02** A cc is not a request. `Hi @DevK cc @PriyaS @NikhilB` asks Dev for something
  and copies the others. A name only inside a `cc` / `fyi` / `copying` / `looping in` list
  does not surface the task. The list ends where prose resumes, so `Done cc @NikhilB,
  separately @PriyaS can you confirm?` still counts for Priya. `ignoreCcOnlyMentions`
  switches it off.

- **2026-09-02** Teamwork auto-subscribes anyone you mention as a follower, so "is a
  follower" is true for every mention and cannot separate `awaiting-response` from
  `action-requested`. Assignment is what separates them.

- **2026-09-02** Teamwork's v3 tasks endpoint silently ignores `assigneeUserIds` and
  returns everything. `responsiblePartyIds` is the parameter it honours. A task's project
  id lives at `tasklist.meta.projectId`, not at the top level.

- **2026-09-02** Teamwork does not expose the `@handle` anywhere in its API. The only
  reliable source is a markdown mention in a real comment, `[@ArjunR](/app/people/400002)`,
  which ties a handle to a user id (`src/teamwork/discovery.ts`).

- **2026-09-02** `chat.postMessage` needs no `im:write`. Given a user id as the channel it
  opens the DM itself. The bot needs only `chat:write`, `users:read`, `users:read.email`.

- **2026-09-02** Slack lookup methods (`users.lookupByEmail`, `auth.test`) reject a JSON
  body with `invalid_arguments` and must be form-encoded. Methods that take structured
  payloads accept JSON (`src/slack/client.ts`).

- **2026-09-02** `search.messages` rejects bot tokens outright, and a user token sees only
  its owner's Slack. So each recipient needs their own `xoxp-` token
  (`SLACK_USER_TOKEN_<ID>`), and someone without one gets no Slack section rather than a
  list built from a colleague's view that would silently omit their DMs.

- **2026-09-02** Slack search is fuzzy: querying `@handle` returns near-misses. Results
  are re-filtered on the literal `<@UID>` token. A match object has no `thread_ts`; the
  thread root is a query parameter on the `permalink`. Forwarding a message quotes its
  mention, so you can "mention" yourself; those are dropped.

- **2026-09-02** Without `slackBroadcastThreshold` (default 5) a daily stand-up bot that
  tags seven people appeared in the digest every single day.

- **2026-09-02** Gemini's non-streaming `generateContent` regularly returned nothing on
  this network while `streamGenerateContent` completed, slowly. And the API key in the
  `X-goog-api-key` header was dropped outright (0 bytes, no status) while the query-string
  form returned normally, consistent with a proxy filtering on that header
  (`src/llm/gemini.ts`). One attempt only; retrying a blocked endpoint doubles the wait.

- **2026-09-02** Oracle's Always Free ARM shape needs `arch=arm64` in the Docker apt line;
  the usual copy-pasted `arch=amd64` installs nothing. The shape ships with no swap, so the
  TypeScript and Vite builds were OOM-killed with nothing in the output but `Killed`. 2 GB
  of swap fixes it (`scripts/provision-host.sh`). "Out of host capacity" on creation is
  the free-tier squeeze, not a mistake; try another availability domain or region.

- **2026-09-02** Render free instances sleep after 15 minutes idle. Nothing requests the
  app at 09:00, so a sleeping instance never sends. A paid plan and a persistent disk at
  `/app/data` are both required, and `DASHBOARD_PASSWORD` because the URL is public.

- **2026-09-02** A stale process holding the port made launchd crash-loop in silence.
  `EADDRINUSE` now prints the `lsof -nP -iTCP:4310 -sTCP:LISTEN` command to find it.

- **2026-09-02** `setTimeout` overflows past about 24.8 days and fires immediately. The
  scheduler clamps delays and re-arms when the clamped timer fires early.

- **2026-09-02** A run failing during the scan threw before any bookkeeping, so a lost run
  left no trace in the dashboard. Scan failures are now written to the run history.

- **2026-09-28. The evening digest went out twice (21:08 and 21:39) after the Mac slept.**
  The scheduler armed one long `setTimeout` for the 21:00 slot. Node timers only count time
  the machine is awake, and the Docker VM on the laptop stops with it: two naps in the
  evening (19:05–19:17, 19:47–20:13) pushed the timer to 21:37. Meanwhile the 15-minute
  catch-up poll, which checks the wall clock, saw the slot owed at 21:06 and sent it. The
  late timer then fired without asking whether the slot was already satisfied. Fix: the timer
  now re-arms every minute (`TIMER_TICK_MS`), so a nap can only delay a slot by the part of
  the nap that overlaps it, and the timer path checks `slotAlreadyDelivered` before firing,
  the same test the catch-up poll already used. Pinned by three tests in
  `src/__tests__/scheduler.test.ts`.
