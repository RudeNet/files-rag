# Contributing

Thanks for helping. Bug reports, ideas and pull requests are welcome.

## Before you start

- Read [docs/architecture.md](docs/architecture.md): it explains the indexing phases, the catalog schema and how
  the agent uses tools.
- For larger changes, open an issue first so the approach can be agreed on.

## Development setup

```bash
git clone https://github.com/<you>/files-rag.git
cd files-rag
./scripts/setup.sh                 # Windows: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Use a separate catalog while developing so your real map is not touched:

```bash
export FILERAG_CATALOG_DB=/tmp/dev-catalog.db FILERAG_CHATS_DB=/tmp/dev-chats.db FILERAG_SETTINGS=/tmp/dev-settings.json
.venv/bin/python -m filerag add ./some-test-folder
.venv/bin/python -m filerag web
```

(PowerShell: `$env:FILERAG_CATALOG_DB = "$env:TEMP\dev-catalog.db"`, and so on.)

## Guidelines

- **Never delete, move or modify the user's files.** The app only reads. This is the most important rule.
- Keep everything local: no telemetry, no calls to services other than the configured Ollama server.
- Match the surrounding style: short functions, docstrings that say *why*, no new dependencies unless they are
  clearly worth it (they go in `requirements.txt` with a comment).
- New settings go in `filerag/config.py` (environment variable `FILERAG_*`), and in `filerag/settings.py` if they
  should be changeable from the UI. Document them in [docs/configuration.md](docs/configuration.md).
- Catalog schema changes must migrate old catalogs: add new columns to `NEW_COLUMNS` in `filerag/catalog.py`.
- New agent tools: add the schema to `TOOLS` in `filerag/agent.py`, the call in `Agent._run_tool`, a digest in
  `filerag/context.py`, a step renderer in `web/static/app.js`, and document it in [docs/agent.md](docs/agent.md).
- Update [CHANGELOG.md](CHANGELOG.md) under an *Unreleased* heading.

## Checking your change

There is no test suite yet. Before a pull request:

1. `python -m filerag doctor` passes.
2. Map a small folder with a mix of file types, run `python -m filerag scan`, and check `find`, `search`, `dupes`,
   `folders` and `cleanup`.
3. Open the web UI, check the Files page and the browser console for errors, and ask the agent one question.

## License

By contributing you agree that your contribution is licensed under the
[GNU General Public License v3.0](LICENSE).
