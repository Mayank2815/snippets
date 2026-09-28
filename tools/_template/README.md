# <tool-name>

<!-- One or two sentences. What does this tool do, and for whom? A reader should know
     within ten seconds whether this is the tool they are looking for. -->

## What it does

<!-- The behaviour, in plain English. What goes in, what comes out, when it happens.
     If it sends messages, say what and to whom. If it changes something, say what. -->

## Run it locally

<!-- The exact commands, in order, from a fresh clone. Include installing dependencies,
     copying .env.example to .env, and the command that starts it. Say which port it
     listens on. -->

```bash
cp .env.example .env    # fill in the values below
```

## Deploy

<!-- Where it runs (VM container or the viewer's own Mac), how to get it there, and how
     to check it is up. A VM tool ships a Dockerfile and docker-compose.yml, answers
     /healthz, and binds to loopback; see knowledge/conventions.md. A Mac tool ships an
     install and run script. -->

## Environment variables

<!-- Every variable the tool reads. Required or optional, what it is for, where to get
     the value. Never the value itself. Keep .env.example in step with this table. -->

| Variable | Required | What it is for |
|---|---|---|
| `PORT` | no | Listening port. Defaults to the number in `knowledge/conventions.md`. |

## Tests

<!-- The one command that runs them, from inside this folder. -->

```bash
```

## Layout

<!-- A short tree of the folders that matter and what each holds. -->

## More

- `knowledge/intro.md` for the longer story
- `knowledge/architecture.md` for how it is put together
- `knowledge/lessons.md` for what went wrong and what was done about it
- `knowledge/cheatsheet.md` for commands, ports, endpoints and variables
