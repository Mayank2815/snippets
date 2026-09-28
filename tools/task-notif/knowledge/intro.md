# task-notif: introduction

Teamwork is where the team's tasks live. Slack is where the team actually looks. This tool
reads Teamwork every day and sends each person a Slack direct message about the tasks that
need them, and another about what they did. Nobody has to open Teamwork to know what is
waiting, and nobody has to reconstruct their day for stand-up.

## What it sends, and to whom

Each recipient gets their own messages, built from their own view of Teamwork and, if they
have given a Slack user token, their own Slack.

| Message | When (default) | What it holds |
|---|---|---|
| Morning reminder | 09:00, Monday to Friday | Yesterday's stand-up summary (Monday's covers the whole weekend), then the tasks that need them today, then any Slack thread that tagged them and is still unanswered. |
| Evening digest | 21:00, Monday to Friday | What they did today: tasks commented on (with pull-request links pulled out), time logged, tasks completed, other edits, work assigned to them today, questions answered and still open in Teamwork and Slack, calls that happened. Ends with a short stand-up summary. |
| Week in review | 19:00 Friday | The same digest over the week just worked, Monday to now. Teamwork only. |

Every task row carries buttons. **Done** hides the item until someone says something new
on it. On a question row, **Reply** opens a form that posts a comment to Teamwork as that
person. On an overdue row, a date picker moves the due date and a button completes the
task. The buttons work over Slack Socket Mode, so nothing has to be reachable from the
internet.

A dashboard on port 4310 holds everything except the tokens: recipients, schedule, rules,
filters, threads marked done, run history, a preview of what would be sent, and a
date-range report of what each person did between two dates (for appraisals).

## How it decides what to send

Every task in the workspace goes through two stages.

**Stage 1, hard exclude.** A task where the person appears nowhere, neither assigned nor
mentioned in any comment, can never appear. This is what keeps long-forgotten tasks out.
Being a follower does not count, because Teamwork subscribes everyone you mention
automatically.

**Stage 2, inclusion rules.** Whatever survives is included if it trips at least one rule.
A task that trips several is reported once, under the highest-priority rule.

| Rule | Fires when | Links to |
|---|---|---|
| `awaiting-response` | Task is assigned to them, a comment mentions them, and they have not replied since | the comment |
| `action-requested` | Task is assigned to someone else, and a comment mentions them with an ask ("pls check", "can you review") | the comment |
| `overdue` | Task is assigned to them and the due date has arrived or passed | the task |

Three facts shape the rules. **Assignment** is the only thing that tells rule one from rule
two, because "follower" is true for every mention. **Mentions are matched on the
`@handle`**, word-boundary exact, because Teamwork writes mentions as plain text with no
user id. **A cc is not a request**: a name that appears only inside a `cc` / `fyi` /
`copying` list does not surface the task.

On top of the rules, tasks are scoped to a slice of the board (Sprint Backlog through BA or
QA Signed-off by default) so grooming-stage and already-shipped work stay out. A board
whose columns cannot be recognised is left unfiltered rather than emptied.

## What it deliberately does not do

- **It does not guess intent.** When a message tags several people and a colleague
  replies, the row is marked and sorted lower, never dropped. "Please check this" is
  discharged by one person; "can you both raise the PR" is not. Same shape, opposite
  answer.
- **It does not write narrative on its own.** The stand-up summary is assembled from
  counted facts. Gemini can be switched on to phrase it, and it falls back to the local
  version whenever the call fails. Direct message text is withheld from Gemini by default.
- **It does not read anyone's Slack through someone else's token.** No user token means no
  Slack section, rather than a partial one that looks complete.
- **It does not post as the wrong person.** Reply, complete and move-date need that
  person's own Teamwork token, because Teamwork has no way to act on someone's behalf.
  Without one, the row keeps only its Done button.
