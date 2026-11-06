# Security policy

Files Explorer is a local application: it reads the folders you map, stores what it learns in local SQLite files
and talks only to your local Ollama server. Read [docs/security.md](docs/security.md) for the threat model and the
safe ways to run it.

## Supported versions

Only the latest release receives fixes.

| Version | Supported |
|---|---|
| 0.1.x | ✅ |

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private
[security advisory](../../security/advisories/new) form on this repository, or contact
[rudenetworks](https://rudenetworks.com).

Include the version (`python -m filerag --version`), your OS, how the server was started (host / port), and steps
to reproduce. You should get an answer within a week.

Things that are **in scope**: reading files outside the mapped folders, bypassing API-key checks, cross-site
attacks against the web UI, code execution through crafted files (PDF, Office, archives, images), and anything
that makes the app delete, move or modify files.

Known and documented, **not** vulnerabilities: the web UI has no login, and starting it with `--host 0.0.0.0`
exposes every mapped file to the network ([docs/security.md](docs/security.md)).
