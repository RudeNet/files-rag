# Configuration

Files Explorer is configured in two layers:

1. **Environment variables** (`FILERAG_*`) set the defaults when the process starts.
2. **`settings.json`**, written by the *Settings* page, overrides them. Values changed in the UI apply immediately
   (no restart) and are used by the CLI too.

To go back to a default, delete the key from `settings.json` (or the whole file) and restart.

Browser-only preferences (theme, text size, Enter-to-send, expanded steps, token stats) are stored in the browser's
local storage, not on the server.

## Data files

| Variable | Default | Contents |
|---|---|---|
| `FILERAG_CATALOG_DB` | `./catalog.db` | The file map: mapped folders, every file, text passages, FTS index, embeddings |
| `FILERAG_CHATS_DB` | `./chats.db` | Chats, notes, API key hashes |
| `FILERAG_SETTINGS` | `./settings.json` | Values changed in the Settings page |

`./` is the project folder. SQLite runs in WAL mode, so you will also see `-wal` and `-shm` files next to the
databases. Back up by stopping the server and copying the files. The catalog can always be rebuilt from your
folders; `chats.db` holds the only copy of your chats and notes.

## Ollama and models

| Variable | Default | UI | Meaning |
|---|---|---|---|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | | Ollama server |
| `FILERAG_CHAT_MODEL` | `qwen3:14b` | Model → Chat model | Must support tool calling |
| `FILERAG_EMBED_MODEL` | `nomic-embed-text` | | Embedding model. **Changing it requires `python -m filerag reindex`** |
| `FILERAG_EMBED_DOC_PREFIX` | `search_document: ` | | Prefix for passages (nomic-style models); `""` for models without prefixes |
| `FILERAG_EMBED_QUERY_PREFIX` | `search_query: ` | | Prefix for queries |
| `FILERAG_NUM_CTX` | `32768` | Model → Context window | Context window in tokens (8192, 16384, 24576, 32768 in the UI) |
| `FILERAG_NUM_GPU` | `999` | | Layers offloaded to the GPU (999 = all) |
| `FILERAG_TEMPERATURE` | `0.2` | Model → Temperature | 0–2 |
| `FILERAG_THINK` | `0` | Model → Reasoning mode | `1` = qwen3-style reasoning before answering |
| `FILERAG_KEEP_ALIVE` | `30m` | | How long Ollama keeps the model loaded after a request |

## Agent

| Variable | Default | UI | Meaning |
|---|---|---|---|
| `FILERAG_ANSWER_LENGTH` | `normal` | Appearance → Answer length | `concise`, `normal`, `detailed` |
| `FILERAG_MAX_TOOL_ROUNDS` | `8` | Model → Max tool calls per answer | 1–20 |
| `FILERAG_CONTEXT_RESERVE` | `3500` | | Tokens of the context kept free for tool schemas and the answer |

## Mapping

| Variable | Default | UI (Mapping) | Meaning |
|---|---|---|---|
| `FILERAG_CONTENT_INDEX` | `1` | Read file contents | `0` = names and metadata only (fastest; no content search) |
| `FILERAG_EMBED_CONTENT` | `1` | Semantic search | Embed passages for meaning-based search; `0` = keyword search only, no GPU work |
| `FILERAG_MAX_CONTENT_MB` | `20` | Largest file to read (MB) | Larger files are mapped by name only (1–500) |
| `FILERAG_SNIFF_UNKNOWN` | `1` | Detect text in unknown extensions | Read files with unfamiliar extensions when their bytes look like text |
| `FILERAG_FIND_DUPLICATES` | `1` | Find duplicates | Compare same-size files to find identical copies |
| `FILERAG_READ_MEDIA` | `1` | Read image details | Pixel size, EXIF date taken and camera (needs Pillow) |
| `FILERAG_LIST_ARCHIVES` | `1` | List files inside archives | Index the file names inside .zip / .tar archives |
| `FILERAG_SKIP_HIDDEN` | `1` | Skip hidden files and folders | Dot-files/folders and Windows hidden system folders |
| `FILERAG_EXCLUDE_DIRS` | `.git,.svn,.hg,node_modules,__pycache__,.venv,venv,.idea,.vscode,.mypy_cache,.pytest_cache,.tox,$RECYCLE.BIN,System Volume Information` | Folders to skip | Folder **names** (not paths) never entered, comma-separated, case-insensitive |
| `FILERAG_EMBED_FILE_CHUNKS` | `100` | | Passages per file that get embeddings (~150 KB of text); keyword search covers the whole file |
| `FILERAG_CHUNK_CHARS` | `1500` | | Passage size in characters (`reindex` after changing) |
| `FILERAG_CHUNK_OVERLAP` | `200` | | Overlap between passages (`reindex` after changing) |

Turning *Semantic search*, *Find duplicates* or *Read image details* on in the UI starts a background job that
fills in what is missing for files already mapped.

## Retrieval

| Variable | Default | Meaning |
|---|---|---|
| `FILERAG_CANDIDATES` | `60` | Results taken from each retriever (keyword, semantic) before fusion |
| `FILERAG_EMBED_BATCH` | `32` | Passages per embedding request |
| `FILERAG_EMBED_MAX_CHARS` | `2000` | Passage text sent to the embedding model is cut to this length |

## API

| Variable | Default | UI | Meaning |
|---|---|---|---|
| `FILERAG_API_ENABLED` | `0` | API → Enable API access | `1` enables `/api/v1` (API key still required) |

## Server

The bind address and port are command-line options, not settings: `python -m filerag web --host 127.0.0.1
--port 8200`, or `scripts/start.ps1 -BindHost … -Port …`, or `HOST=… PORT=… ./scripts/start.sh`.

## Example: a light setup for a laptop without a GPU

```powershell
$env:FILERAG_CHAT_MODEL = "qwen3:4b"
$env:FILERAG_NUM_CTX = "16384"
$env:FILERAG_EMBED_CONTENT = "0"        # keyword content search only
scripts\start.ps1
```

## Example: names only for a huge media drive

```bash
FILERAG_CONTENT_INDEX=0 FILERAG_CATALOG_DB=~/media-catalog.db .venv/bin/python -m filerag add /mnt/media
```

Duplicates, folder sizes, clean-up and image details still work; content search does not.

## `settings.json` example

```json
{
  "chat_model": "qwen3:8b",
  "temperature": 0.2,
  "num_ctx": 24576,
  "think": false,
  "max_tool_rounds": 8,
  "answer_length": "concise",
  "api_enabled": false,
  "content_index": true,
  "embed_content": true,
  "max_content_mb": 20,
  "sniff_unknown": true,
  "find_duplicates": true,
  "read_media": true,
  "list_archives": true,
  "skip_hidden": true,
  "exclude_dirs": ".git,node_modules,.venv,venv,__pycache__"
}
```
