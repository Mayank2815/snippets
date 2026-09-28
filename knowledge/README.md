# Repo knowledge

Two kinds of knowledge live in this repo, in two places.

| Where | What belongs there |
|---|---|
| `knowledge/` (this folder) | Anything that is true across tools: the port table, naming rules, where data volumes live on the VM, the SSH tunnel pattern, the Docker patterns every VM tool follows. If a fact would have to be copied into two tools' folders, it belongs here. |
| `tools/<name>/knowledge/` | Anything true of one tool only: what it does, how it is built, the gotchas it hit, its commands and endpoints. Four files, always the same names, so you know where to look. |

## The four per-tool files

| File | Answers |
|---|---|
| `intro.md` | What does it do, for whom, and what does it deliberately not do? |
| `architecture.md` | How is it put together? Which file does what? How does it get deployed? |
| `lessons.md` | What went wrong, when, and what was done about it? One dated bullet per lesson. |
| `cheatsheet.md` | Which command, port, endpoint or variable do I need right now? |

## Rules of thumb

- Write a lesson down the day it is learned, with the date. An undated lesson is a rumour.
- When a tool's behaviour changes, update its `architecture.md` in the same pull request.
- Keep `conventions.md` current when a port is taken or a pattern changes. It is the
  first thing a new tool's author reads.
- Plain English. Short sentences. Expand an acronym the first time it appears.
