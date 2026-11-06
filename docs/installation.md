# Installation

Files Explorer is a Python application that talks to a local [Ollama](https://ollama.com) server. Everything runs
on your computer.

## Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10/11, Linux, macOS | |
| Python | 3.10 | 3.12 |
| Ollama | any recent version with tool calling | latest |
| GPU | none (CPU works, slowly) | NVIDIA with 12–16 GB VRAM for `qwen3:14b` |
| RAM | 8 GB | 16 GB+ (embeddings are held in RAM: ~1.5 KB per text chunk) |
| Disk | the catalog is roughly 5–15 % of the size of the text it indexes | SSD |

Without a GPU, mapping, name search, keyword search, duplicates, folder sizes and clean-up all work at full speed:
only the chat agent and the semantic (embedding) part of content search need the models.

### Choosing a chat model

The chat model must support **tool calling**. Pick by GPU memory:

| VRAM | Chat model | Notes |
|---|---|---|
| 16 GB+ | `qwen3:14b` (default) | Best answers; ~9 GB with a 32k context |
| 8–12 GB | `qwen3:8b` | Good; set the context window to 16k–24k if VRAM is tight |
| 4–6 GB | `qwen3:4b`, `llama3.2:3b` | Simple questions only |
| CPU | `qwen3:4b` | Expect a minute or more per answer |

The embedding model `nomic-embed-text` (~270 MB) is small and runs anywhere.

## Quick install (recommended)

### Windows (PowerShell)

```powershell
git clone https://github.com/<owner>/files-rag.git
cd files-rag
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Options: `-ChatModel qwen3:8b` uses a different chat model (saved to `settings.json`), `-EmbedModel NAME`
changes the embedding model, `-SkipModels` skips the model downloads (useful after `git pull`).

### Linux / macOS

```bash
git clone https://github.com/<owner>/files-rag.git
cd files-rag
./scripts/setup.sh
```

Options as environment variables: `CHAT_MODEL=qwen3:8b ./scripts/setup.sh`, `EMBED_MODEL=...`,
`SKIP_MODELS=1`.

### What the setup script does

1. Checks for Python 3.10+.
2. Creates a virtual environment in `.venv` and installs [`requirements.txt`](../requirements.txt).
3. Checks that Ollama is installed and starts `ollama serve` if it is not running. If Ollama is missing it tells
   you how to install it (`winget install --id Ollama.Ollama -e` on Windows,
   `curl -fsSL https://ollama.com/install.sh | sh` on Linux) and stops.
4. Pulls the chat and embedding models.
5. Runs `python -m filerag doctor` to check everything.

## Manual install

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt          # Windows: .venv\Scripts\pip install -r requirements.txt
ollama pull qwen3:14b
ollama pull nomic-embed-text
.venv/bin/python -m filerag doctor
```

Python packages and why they are needed:

| Package | Used for |
|---|---|
| `flask` | Web UI and HTTP API |
| `ollama` | Client for the local Ollama server (chat and embeddings) |
| `numpy` | In-memory vector search |
| `rich` | Terminal output of the CLI |
| `pypdf` | PDF text |
| `python-docx`, `python-pptx`, `openpyxl` | Word, PowerPoint and Excel text |
| `pillow` | Image pixel size and EXIF data (optional: without it image details are skipped) |

## First run

```powershell
.venv\Scripts\python -m filerag add D:\Projects        # or map folders later from the Files page
scripts\start.ps1                                       # Linux/macOS: ./scripts/start.sh
```

Open **http://127.0.0.1:8200**. The first scan of a large folder runs in the background: names, sizes and dates
are searchable within seconds to minutes; contents and embeddings follow. See the [User guide](user-guide.md).

Start options: `scripts\start.ps1 -Port 8300`, `scripts\start.ps1 -BindHost 0.0.0.0` (Linux/macOS:
`PORT=8300 HOST=0.0.0.0 ./scripts/start.sh`). Read [Security](security.md) before binding to anything other than
`127.0.0.1`.

### Running it at login (optional)

- **Windows**: Task Scheduler → *Create Basic Task* → trigger *At log on* → action *Start a program*:
  `powershell.exe`, arguments `-WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\path\to\files-rag\scripts\start.ps1"`.
- **Linux (systemd user service)**: `~/.config/systemd/user/files-explorer.service`:

  ```ini
  [Unit]
  Description=Files Explorer
  After=network.target

  [Service]
  WorkingDirectory=%h/files-rag
  ExecStart=%h/files-rag/.venv/bin/python -m filerag web
  Restart=on-failure

  [Install]
  WantedBy=default.target
  ```

  then `systemctl --user enable --now files-explorer`.

## Updating

```bash
git pull
./scripts/setup.sh                    # or: SKIP_MODELS=1 ./scripts/setup.sh
```

Your catalog, chats, notes and settings are kept: the catalog is migrated automatically when a new version adds
columns. New features that need data the old catalog lacks (for example duplicate hashes or image details) are
filled in by the background worker the next time the web app starts.

## Uninstalling

Stop the server and delete the folder. Your data lives only in `catalog.db`, `chats.db` and `settings.json` in
that folder (unless you moved them with `FILERAG_CATALOG_DB` etc.). Nothing is installed system-wide apart from
Ollama and its models (`ollama rm qwen3:14b nomic-embed-text`). **Your mapped files are never touched.**
