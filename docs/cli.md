# CLI reference

```
python -m filerag <command> [options]
```

Run it with the virtual environment's Python: `.venv\Scripts\python -m filerag …` on Windows,
`.venv/bin/python -m filerag …` on Linux / macOS. The CLI uses the same catalog, chats and `settings.json` as the
web app, so both can be used side by side.

`python -m filerag --version` prints the version; `python -m filerag <command> -h` shows a command's options.

| Command | Purpose |
|---|---|
| [`doctor`](#doctor) | Check catalog, GPU, Ollama and models |
| [`add`](#add) | Map folders and index them |
| [`roots`](#roots) | List mapped folders |
| [`scan`](#scan) | Rescan mapped folders (incremental) |
| [`remove`](#remove) | Forget mapped folders |
| [`find`](#find) | Find files by name and metadata |
| [`stats`](#stats) | Counts and sizes grouped by a field |
| [`dupes`](#dupes) | Sets of identical files |
| [`folders`](#folders) | Folder sizes |
| [`cleanup`](#cleanup) | Space that could be freed |
| [`show`](#show) | Details of one file |
| [`search`](#search) | Content search (no LLM) |
| [`reindex`](#reindex) | Re-read and re-embed all contents |
| [`ask`](#ask) | One question to the agent |
| [`chat`](#chat) | Interactive agent session |
| [`web`](#web) | Start the web app |

## Common filter options

`find`, `stats`, `dupes`, `folders` and `search` accept these ([syntax](search-and-filters.md#filters)):

| Option | Example |
|---|---|
| `--name TEXT` (positional for `find`) | `--name "invoice 2024"`, `--name "*.log"` |
| `-e, --ext EXT` | `-e pdf`, `-e "py,js"` |
| `-t, --category CAT` | `-t image`, `-t code` |
| `-f, --folder FRAGMENT` | `-f Projects` |
| `--min-size SIZE` / `--max-size SIZE` | `--min-size 100MB` |
| `--after DATE` / `--before DATE` | `--after 2024-05`, `--before 1y` |

Most listing commands also take `-n, --limit N` and `--json` (machine-readable output).

---

## doctor

```
python -m filerag doctor
```

Prints the catalog location and size (folders, files, readable files, chunks, embedded chunks, pending work,
unreadable files), the GPU (via `nvidia-smi`), whether Ollama is reachable, and whether the chat and embedding
models are pulled (with the `ollama pull` command if not). Exit code 1 when something is missing.

## add

```
python -m filerag add PATH [PATH ...]
```

Maps one or more folders and runs every phase in the foreground: scan, duplicate check, image details, contents,
embeddings. Adding a folder that is already mapped rescans it.

```powershell
python -m filerag add D:\Projects "C:\Users\me\Documents"
```

## roots

```
python -m filerag roots
```

Table of mapped folders: id, path, files, size, files with content, last scan, status.

## scan

```
python -m filerag scan [ID|PATH ...]
```

Incremental rescan of the given folders (default: all), followed by the other phases for what changed. Suitable
for a scheduled task (Task Scheduler / cron) to keep the map current.

## remove

```
python -m filerag remove ID|PATH [...]
```

Forgets folders: their files, passages and vectors are removed from the catalog. **Nothing is deleted on disk.**

## find

```
python -m filerag find [NAME] [filters] [--sort KEY] [--order asc|desc] [-n N] [--json]
```

`--sort`: `name` (default), `size`, `modified`, `created`, `path`, `ext`, `taken`. Default limit 50.

```bash
python -m filerag find "*.iso" --sort size
python -m filerag find -t video --min-size 1GB --before 2023
python -m filerag find invoice -e pdf --after 2024 --json
```

## stats

```
python -m filerag stats [BY] [filters] [--sort count|size|name] [-n N] [--json]
```

`BY`: `ext` (default), `category`, `folder`, `root`, `year`, `month`, `content`.

```bash
python -m filerag stats category --sort size
python -m filerag stats year -t image          # photos per year
python -m filerag stats folder -e mp4 --sort size
```

## dupes

```
python -m filerag dupes [filters] [--sort wasted|copies|size] [-n N] [--json]
```

Sets of identical files, each with its copies and reclaimable space; the header shows the total and how many files
are still being compared. Default limit 20 sets.

```bash
python -m filerag dupes -t image -n 50
python -m filerag dupes -f Downloads --sort copies
```

## folders

```
python -m filerag folders [PATH] [filters] [-n N] [--json]
```

Sub-folders of `PATH` (default: the mapped folders) with recursive size, file count and newest file, largest first.
Filters restrict what is counted (`folders D:\ -t video` = where the videos are).

## cleanup

```
python -m filerag cleanup
```

Table of what could be freed: duplicate copies, empty files, temp/backup files, build output, large files
unchanged for a year, installers and disk images. Nothing is deleted.

## show

```
python -m filerag show FILE [--json]
```

`FILE` is a ref (`F12`), a full path or a file name. Prints the name, type, size, dates, path and content status.

## search

```
python -m filerag search "QUERY" [filters] [--mode hybrid|keyword|semantic] [-n N] [--json]
```

Content search without the LLM: matching passages with file, line or page. Default 10 results.

```bash
python -m filerag search "connection string" -t config
python -m filerag search "def parse_date" -e py --mode keyword
```

## reindex

```
python -m filerag reindex
```

Deletes all embeddings, marks every readable file for re-reading, and runs the content and embedding phases. Needed
after changing `FILERAG_EMBED_MODEL`, `FILERAG_CHUNK_CHARS` or `FILERAG_CHUNK_OVERLAP`. Can take a long time on
large catalogs.

## ask

```
python -m filerag ask "QUESTION" [-v]
```

One question to the agent; tool calls are printed in cyan and the answer streams to the terminal. `-v` also prints
tool results and reasoning. Nothing is saved.

## chat

```
python -m filerag chat [-v]
```

Interactive session with follow-ups. Commands inside the chat:

| Command | Effect |
|---|---|
| `/context` | Context usage and the remembered files |
| `/reset` | Clear the conversation |
| `/verbose` | Toggle tool output |
| `/exit`, `/quit` | Leave (also Ctrl+C / Ctrl+D) |

## web

```
python -m filerag web [--host HOST] [--port PORT]
```

Starts the web app (default `127.0.0.1:8200`). `--host 0.0.0.0` makes it reachable from other machines: read
[Security](security.md) first. On start it resumes unfinished background work (pending contents, image details,
duplicate checks, missing embeddings).
