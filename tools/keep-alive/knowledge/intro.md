# Keep Alive — what and why

## The problem

DevOps stop any dev or QA instance that has seen no traffic for twenty minutes, to save
money. Starting one again takes five to ten minutes. A fix-and-verify loop — write a change,
deploy it, open the page, look — is easily longer than twenty minutes, so the instance is
routinely gone by the time you go to check your work, and you sit through a restart.

## What this does

While an instance is "kept", the service pings it every few minutes so it never looks idle.
If it goes down anyway (someone else's idle window, a deploy, a manual stop), the service
presses the same Start button the maintenance page shows, and keeps looking every minute
until the instance is back. The dashboard shows what the last look saw and when the next
one is due.

Keeping is deliberately time-boxed: one press of Start keeps an instance for a fixed number
of hours (eight by default). The idle stop exists for a reason, and a toggle someone forgot
about should not keep a server up all night.

## Who uses it

Anyone working on a dev or QA instance: developers mid-fix, QA running through a test
plan, a demo that must not go down halfway. One page, no login locally — paste the
instance URL, press Start, get on with the work.

## Where it came from

It was a panel inside `task-notif` (the Teamwork → Slack reminder service) until
28 September 2026, when it was carved out into its own service so the two could be
deployed and restarted independently. The keeper logic, the tests and the panel moved
across unchanged in substance; the store is new (its own JSON file rather than a key in
task-notif's), with a one-off import so nobody had to re-add their instances.
