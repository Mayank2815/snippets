# Contributing

Two things live here: the contract every tool folder must satisfy, and how a change gets
into `main`.

## The tool contract

Every folder under `tools/` (except `_template`) must have all of the following. A pull
request that adds a tool without them is not ready.

| Requirement | What it means |
|---|---|
| `README.md` | Purpose, how to run it locally, how to deploy it, and every environment variable it reads. Start from `tools/_template/README.md`. |
| `knowledge/` | Four files: `intro.md` (what it does and for whom), `architecture.md` (how it is put together, with file paths), `lessons.md` (dated gotchas), `cheatsheet.md` (commands, ports, endpoints, variables). Start from `tools/_template/knowledge/`. |
| Own dependency manifest | Its own `package.json`, `requirements.txt`, `pyproject.toml` or equivalent, inside its folder. No shared lockfile at the repo root. |
| Own tests | Runnable from inside the folder with one command that the README names. |
| Own state | It reads and writes only its own data directory. It never touches another tool's files, database or store. If two tools need the same data, one of them exposes an endpoint. |
| Own deploy story | The README says how it reaches its host and how you check it is up. A VM tool ships a `Dockerfile` and `docker-compose.yml`; a Mac tool ships an install and run script. |
| Separate process per host | A tool that needs a different host than another tool (for example one that must run on the viewer's Mac) is a separate process and a separate folder. Never fold two hosts into one service. |

The shared conventions that make these fit together (ports, naming, where volumes live,
the healthcheck and entrypoint pattern) are in `knowledge/conventions.md`.

## How a change lands

**One branch per change.** Branch from an up-to-date `main`. Name it by type and subject,
for example `feat/keep-alive-boot-poll` or `docs/task-notif-lessons`.

**Open a pull request.** Say what changed, why, and how you checked it. UI changes get a
screenshot.

**Rebase-merge, never squash across tools.** A squash turns several tools' commits into
one, which makes `git log -- tools/<name>` useless and makes a revert of one tool's change
take the others with it. Rebase onto `main` before merging, and merge with rebase so each
commit keeps its own scope.

**Commit message format.**

```
type(scope): summary in one line

Problem: what was wrong or missing, in a sentence or two
Solution: what this commit does about it
```

`type` is one of `feat`, `fix`, `refactor`, `docs`, `test`, `chore`. `scope` is the tool
folder name (`task-notif`, `keep-alive`, `emulation-engine`, `workbench`) or `repo` for
changes outside any tool. Keep one tool per commit where you can.

## Secrets

Nothing secret goes into git. `.env` files are ignored everywhere in the repo
(see `.gitignore`), and so are `data/` directories. Credentials live in the tool's `.env`
on the machine that runs it, or in `.claude/gitignore/creds` for agent sessions. A
`.env.example` with empty values is what gets committed.

If a token ever lands in a commit, treat it as leaked: revoke it, then rewrite history.

## Comments on constants

Every constant, timeout, port, threshold or batch size gets a comment saying **why** that
value, not what it is. The reason is the part the next person cannot recover from the
code.

```ts
// Five minutes leaves room for three failed pings before the twenty-minute idle stop fires.
const PING_MINUTES = 5;
```

If the reason is not known, say so: `// Reason unknown, inherited value.`
