# Workbench: intro

**What it is.** The single dashboard for the snippets repo. One browser tab, one port
(4300 on the VM, reached over an SSH tunnel), and a tab inside it for every tool.

**Why it exists.** The lead asked for every feature to be reachable from one UI. The tools
themselves are deliberately independent (own process, own data, own deploy), so the
workbench is the thin layer that makes them feel like one product without coupling them.

**Who uses it.** Anyone on the team who uses any of the tools. Developers open it over an
SSH tunnel to the VM; the Emulation Engine tab additionally needs the engine running on
the viewer's own Mac.
