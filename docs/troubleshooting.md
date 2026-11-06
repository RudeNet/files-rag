# Troubleshooting & FAQ

Start with the health check; it finds most problems:

```bash
python -m filerag doctor
```

## Installation and startup

**`Python not found` / wrong version.** Install Python 3.10+ from python.org; on Windows tick *Add python.exe to
PATH*. Close and reopen the terminal, then run the setup script again.

**`ModuleNotFoundError` (ollama, flask, PIL, …).** You are using the system Python instead of the virtual
environment. Use `.venv\Scripts\python` (Windows) or `.venv/bin/python`, or the `scripts/start.*` scripts. After a
`git pull`, run the setup script again to install new packages (for example Pillow).

**Port 8200 already in use.** Another instance is running, or another program uses the port. Start on a different
port: `scripts\start.ps1 -Port 8300` / `PORT=8300 ./scripts/start.sh`.

**PowerShell: "running scripts is disabled on this system".** Run the scripts with
`powershell -ExecutionPolicy Bypass -File scripts\start.ps1`.

## Ollama and models

**Status dot is red / "Ollama not reachable".** Start Ollama (the tray app on Windows / macOS, or `ollama serve`).
If it runs on another machine or port, set `OLLAMA_HOST`.

**"model not found".** Pull it: `ollama pull qwen3:14b` and `ollama pull nomic-embed-text`, or use *Settings → Model
→ Download a model*.

**Answers are very slow.**
- Check the GPU line in the sidebar: if VRAM use is tiny, Ollama runs on the CPU. Update the NVIDIA driver and
  Ollama; `ollama ps` shows `100% GPU` when it is right.
- The model plus a 32k context may not fit your GPU: lower *Context window* to 16k or use a smaller model
  (`qwen3:8b`).
- Embedding jobs share the GPU with the chat model. Answers are slower while *Embedding* runs; stop the job or wait.
- Use *Answer length: Concise*; writing is the slow part.
- Turn *Reasoning mode* off.

**The model does not call tools, or invents files.** Use a model with tool calling support (qwen3 family is
tested). Small models (≤4B) follow instructions poorly. Lower the temperature.

**"tool round limit reached".** The question needed more than 8 tool calls. Ask something narrower, or raise *Max
tool calls per answer*.

## Mapping

**A folder shows status `error`.** The folder line shows the message (also `python -m filerag roots`). Common causes: the folder no
longer exists, or permission denied. Fix and click **Rescan**.

**Some files are missing.** Check that they are not in a skipped folder (*Folders to skip*, hidden folders, `.git`,
`node_modules`, …), are not hidden dot-files with *Skip hidden files* on, and that you rescanned after adding them
(there is no file watcher).

**Content search does not find text I know is in a file.** Open the file panel and check *Content*:
- *not readable*: the type is not supported (images, `.doc`, `.rtf`, binaries).
- *too large*: raise *Largest file to read*.
- *pending*: still being read; wait for the job.
- *error*: the file could not be parsed (encrypted or corrupt PDF, …); the message says why.
- A PDF marked *no text layer* is a scan; there is no OCR.
- If it is indexed, try *keyword* mode (CLI/API) with the exact word, or a shorter query.

**Mapping is slow.**
- The first scan of a large disk reads a lot: duplicates hash same-size files, and contents are extracted. Later
  scans only touch changed files.
- Turn off what you do not need: *Semantic search* (largest GPU cost), *Read file contents* for media drives, *Find
  duplicates*.
- Add big generated folders (build output, caches, datasets) to *Folders to skip*.

**Duplicates: "still comparing N files".** The comparison runs after the scan. Wait for the job to finish; the
counts grow until then.

**Image details missing.** Pillow must be installed (`pip install -r requirements.txt`), and *Read image details*
on. HEIC, RAW and SVG files are not read.

**"Load newly indexed vectors" button.** Embeddings finished while the server held an older copy of the vectors;
click it (or it reloads automatically at the end of the job).

## Web UI

**The page looks broken after an update.** Hard-reload (Ctrl+F5 / Cmd+Shift+R) to drop cached JavaScript.

**Show in folder does nothing.** It only works when the browser runs on the same computer as the server. On Linux
it needs `xdg-open`.

**Video or audio does not play.** The browser decides which formats it can play (`.mkv`, `.avi`, `.wmv` often
cannot). Use *Open* to download it.

## Data

**Start over.** Stop the server and delete `catalog.db*` (the map). Chats and notes are in `chats.db`, settings in
`settings.json`. Your files are not affected.

**The catalog is big.** Most of the space is extracted text and FTS index. Turn off *Read file contents* for
folders that do not need it, lower *Largest file to read*, or skip generated folders. `VACUUM` reclaims space after
removing folders: `sqlite3 catalog.db "VACUUM;"` with the server stopped.

**Move the data elsewhere.** Set `FILERAG_CATALOG_DB`, `FILERAG_CHATS_DB` and `FILERAG_SETTINGS` and move the files.

## FAQ

**Does anything leave my computer?** No. The only network peer is your Ollama server. See [Security](security.md).

**Can it delete my duplicates?** No, by design. It shows where they are; delete what you choose in your file
manager. Duplicates are compared by full SHA-256, so files listed together are byte-for-byte identical.

**Can it search inside images (OCR) or transcribe audio?** Not yet.

**Does it watch folders for changes?** No. Click **Rescan** or schedule `python -m filerag scan`.

**Network drives?** Yes, map them like any folder (`\\server\share` or a mapped drive letter). Scans are slower,
and the server must be able to reach them.

**Several catalogs?** Run separate instances with different `FILERAG_CATALOG_DB` and ports.

**Can I use a model from OpenAI / Anthropic / another API?** Not out of the box: the app is built for local Ollama
models. Ollama-compatible servers on your network work via `OLLAMA_HOST`.

**Which languages does content search support?** Any language is stored and searchable by keyword (stemming is
English-only). Semantic search quality depends on the embedding model; `nomic-embed-text` is English-focused, a
multilingual embedding model can be used with `FILERAG_EMBED_MODEL` + `reindex`.
