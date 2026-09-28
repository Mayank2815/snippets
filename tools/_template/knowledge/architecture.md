# <tool-name>: architecture

<!-- How the tool is put together. Name real files and folders so a reader can open
     them. Keep it current: when behaviour changes, this page changes in the same
     pull request. -->

## The pipeline

<!-- Data in, data out, and each stage in between. A short arrow diagram then one
     paragraph per stage, each citing the file that implements it:

     source -> stage A (src/a.ts) -> stage B (src/b.ts) -> destination
-->

## Where state lives

<!-- The data directory, the file or database inside it, what is kept and for how
     long, and how writes are made safe (atomic rename, permissions). -->

## Entry points

<!-- The server, the CLI commands, the scheduled jobs. Which file starts each. -->

## The front end

<!-- If there is a page or dashboard: what framework, how it is built, how it reaches
     the API in development and in production. -->

## Deploy

<!-- How it reaches its host. Dockerfile stages, compose settings, the deploy script,
     the healthcheck, and any alternative hosts (launchd, a PaaS). -->
