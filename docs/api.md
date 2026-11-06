# HTTP API

Files Explorer exposes a JSON API under `/api/v1` for scripts and other programs: find files, search contents,
get statistics, duplicates, folder sizes and clean-up reports, read and download files, map folders, and ask the
agent.

- [Enabling the API](#enabling-the-api)
- [Authentication](#authentication)
- [Errors](#errors)
- [Endpoints](#endpoints)
- [Examples](#examples)

## Enabling the API

The API is **off by default**.

1. *Settings → API → Enable API access* (or start with `FILERAG_API_ENABLED=1`).
2. Type a name under **API keys** (e.g. `backup-script`) and click **Create key**.
3. Copy the key (`frk_…`). **It is shown only once**: only its SHA-256 hash is stored (in `chats.db`).

Keys can be revoked in the same place; the list shows when each key was last used and how often.

The base URL is `http://127.0.0.1:8200/api/v1`. To call the API from another computer, the server must be started
with `--host 0.0.0.0` (see [Security](security.md)).

## Authentication

Send the key in either header:

```
Authorization: Bearer frk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
X-API-Key: frk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

| Status | When |
|---|---|
| 401 | Missing, invalid or revoked key (`WWW-Authenticate: Bearer`) |
| 403 | API access is disabled in Settings |

## Errors

Errors are JSON with a `detail` message and an HTTP status:

```json
{"detail": "date 'last tuesday' not understood - use e.g. 2024, 2024-05, 2024-05-31 or 30d"}
```

| Status | Meaning |
|---|---|
| 400 | Bad parameter (size, date, mode, missing `q`, …) |
| 404 | Unknown file ref, folder or chat |
| 409 | The chat is already answering a question |
| 502 | The model failed (Ollama not reachable, model missing, …) |

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | [`/api/v1`](#get-apiv1) | List of endpoints |
| GET | [`/api/v1/health`](#get-apiv1health) | Status, version, model, catalog size |
| GET | [`/api/v1/files`](#get-apiv1files) | Find files by name and metadata |
| GET | [`/api/v1/files/{ref}`](#get-apiv1filesref) | One file |
| GET | [`/api/v1/files/{ref}/text`](#get-apiv1filesreftext) | A file's text, in order |
| GET | [`/api/v1/files/{ref}/raw`](#get-apiv1filesrefraw) | Download the original file |
| GET | [`/api/v1/search`](#get-apiv1search) | Content search (no LLM) |
| GET | [`/api/v1/stats`](#get-apiv1stats) | Counts and sizes grouped by a field |
| GET | [`/api/v1/duplicates`](#get-apiv1duplicates) | Sets of identical files |
| GET | [`/api/v1/folders`](#get-apiv1folders) | Sub-folders with recursive sizes |
| GET | [`/api/v1/cleanup`](#get-apiv1cleanup) | Space that could be freed |
| GET | [`/api/v1/roots`](#get-apiv1roots) | Mapped folders |
| POST | [`/api/v1/roots`](#post-apiv1roots) | Map a folder |
| POST | [`/api/v1/ask`](#post-apiv1ask) | Ask the agent |

### Filter parameters

`files`, `search`, `stats`, `duplicates` and `folders` accept these query parameters
([syntax](search-and-filters.md#filters)):

`name`, `file`, `ext`, `category`, `folder`, `root` (mapped folder id), `min_size`, `max_size`, `after`, `before`,
`taken_after`, `taken_before`, `min_dim`, `content`.

### File object

Returned in lists:

```json
{
  "ref": "F471",
  "id": 471,
  "name": "manual.pdf",
  "ext": "pdf",
  "category": "document",
  "folder": "D:\\Projects\\tools\\docs",
  "size": 1912861,
  "size_h": "1.8 MB",
  "modified": "2026-10-15 00:40",
  "mtime": 1792014018.49,
  "content": "ready"
}
```

`content` is `ready` (text indexed), `pending`, `none` (not a readable type), `skipped` (too large or content
reading disabled) or `error`. Images may also carry `width`, `height` and `taken`.

---

### GET /api/v1

Lists the endpoints with descriptions and the version.

### GET /api/v1/health

```json
{
  "status": "ok", "version": "0.1.0", "chat_model": "qwen3:14b",
  "files": 27853, "size": 36932666276, "readable": 15349, "pending": 0, "errors": 0,
  "chunks": 569007, "vectors": 31406, "roots": 1,
  "vectors_loaded": 31406, "jobs": 0
}
```

### GET /api/v1/files

Find files. Filters plus:

| Parameter | Default | |
|---|---|---|
| `sort` | `name` | `name`, `size`, `modified`, `created`, `path`, `ext`, `taken` |
| `order` | `desc` for size/dates, else `asc` | `asc` / `desc` |
| `limit` | 100 | 1–1000 |
| `offset` | 0 | for paging |

```json
{"total": 13, "total_size": 15374946, "total_size_h": "14.7 MB", "files": [ /* file objects */ ]}
```

### GET /api/v1/files/{ref}

`ref` is `F471` (or `471`). Returns the file object plus `path`, `rel` (relative to the mapped folder), `root_id`,
`created`, `kind`, `title`, `pages`, `lines`, `chars`, `chunks`, `category_label`, `meta` (format metadata:
PDF creator, image camera and date taken, …), `outline` (sections), `same_folder` (number of files in the same
folder), `copies` (identical files), and `error` / `warning` when set.

### GET /api/v1/files/{ref}/text

Reads the indexed text of a file in order.

| Parameter | Default | |
|---|---|---|
| `chunk` | 0 | start chunk |
| `count` | 6 | chunks to return (1–20) |
| `page` | | start at this page (PDF, slides) |
| `line` | | start near this line (text, code) |

```json
{
  "ref": "F471", "name": "manual.pdf",
  "total_chunks": 1058,
  "chunks": [{"chunk": 0, "page": 1, "line": null, "section": null, "text": "…"}],
  "next_chunk": 1
}
```

Continue with `chunk=<next_chunk>` until `next_chunk` is `null`.

### GET /api/v1/files/{ref}/raw

The original file, streamed from disk (supports range requests). `?download=1` sets
`Content-Disposition: attachment`.

### GET /api/v1/search

Content search, no LLM.

| Parameter | Default | |
|---|---|---|
| `q` | required | query |
| `mode` | `hybrid` | `hybrid`, `keyword`, `semantic` |
| `limit` | 10 | 1–100 passages |
| filters | | |

```json
{
  "count": 1,
  "results": [{
    "ref": "F468", "file_id": 468, "name": "gbdk-win64.zip", "folder": "D:\\Projects\\tools",
    "ext": "zip", "category": "archive", "chunk": 6,
    "section": "Contents of gbdk-win64.zip: 662 files, 18.8 MB unpacked",
    "text": "gbdk/examples/cross-platform/gbdecompress/Readme.md  (229 B)\n…"
  }]
}
```

Passages from text and code files carry `line`, from PDFs and slides `page`.

### GET /api/v1/stats

| Parameter | Default | |
|---|---|---|
| `by` | `ext` | `ext`, `category`, `folder`, `root`, `year`, `month`, `content` |
| `sort` | `count` | `count`, `size`, `name` |
| `limit` | 30 | 1–500 |
| filters | | |

```json
{
  "group_by": "category", "groups": 16,
  "rows": [{"category": "archive", "label": "Archives", "files": 113, "size": 19578441735,
            "size_h": "18.2 GB", "newest": "2026-11-03 14:00"}]
}
```

### GET /api/v1/duplicates

| Parameter | Default | |
|---|---|---|
| `sort` | `wasted` | `wasted` (space freed by keeping one copy), `copies`, `size` |
| `limit` | 20 | 1–200 sets |
| `offset` | 0 | |
| filters | | applied to the files; a set is shown when at least two of its files match |

```json
{
  "groups": 2243, "files": 11095, "wasted": 1985196075, "wasted_h": "1.8 GB",
  "unchecked": 0,
  "results": [{
    "hash": "e2b5f9a8fe41", "size": 221968710, "size_h": "211.7 MB",
    "copies": 2, "wasted": 221968710, "wasted_h": "211.7 MB",
    "files": [ /* file objects */ ]
  }]
}
```

`unchecked` is the number of same-size files still waiting to be compared; while it is above 0 the results can
grow.

### GET /api/v1/folders

| Parameter | Default | |
|---|---|---|
| `path` | | folder to look into; omitted = the mapped folders |
| `limit` | 60 | 1–500 sub-folders |
| filters | | restrict what is counted |

```json
{
  "path": "D:\\Projects", "parent": "D:\\",
  "files_here": 4, "size_here": 10240,
  "files": 27853, "size": 36932666276, "size_h": "34.4 GB", "folders": 12,
  "children": [{"path": "D:\\Projects\\iso", "name": "iso", "files": 31, "size": 18900000000,
                "size_h": "17.6 GB", "newest": "2026-11-03 14:14"}]
}
```

`files_here` / `size_here` count files directly in `path`; `files` / `size` include everything below.

### GET /api/v1/cleanup

```json
{
  "items": [
    {"key": "duplicates", "label": "Duplicate copies", "files": 8852, "size": 1985196075, "size_h": "1.8 GB",
     "filters": {}, "hint": "2,243 sets of identical files; …"},
    {"key": "empty", "label": "Empty files", "files": 45, "size": 0, "size_h": "0 B",
     "filters": {"max_size": "0"}, "hint": "…"},
    {"key": "large_old", "label": "Large files unchanged for a year", "files": 6, "size": 8385328063,
     "size_h": "7.8 GB", "filters": {"min_size": "100MB", "before": "1y"}, "hint": "…"}
  ]
}
```

Keys: `duplicates`, `empty`, `temp`, `build`, `large_old`, `installers`. Pass an item's `filters` to
`/api/v1/files` (or `/api/v1/duplicates` for the first) to list its files. **Nothing is ever deleted.**

### GET /api/v1/roots

```json
[{"id": 1, "path": "D:\\Projects", "status": "ready", "error": null,
  "added": 1792621291.3, "last_scan": 1793708100.2, "last_scan_h": "2026-11-03 14:15",
  "files": 27853, "size": 36932666276, "size_h": "34.4 GB", "readable": 15349, "pending": 0}]
```

`status`: `queued`, `scanning`, `indexing`, `ready`, `error`.

### POST /api/v1/roots

Maps a folder on the server's computer and scans it in the background.

```json
{"path": "D:\\Projects"}
```

→ `{"id": 2, "created": true}` (`created` is `false` when the folder was already mapped; it is rescanned). 404 when
the folder does not exist.

### POST /api/v1/ask

Asks the agent.

| Field | Type | |
|---|---|---|
| `question` | string, required | up to 20,000 characters |
| `save` | bool | save the question and answer as a new chat (visible in the web UI) |
| `chat_id` | string | continue an existing chat (follow-up questions) |
| `stream` | bool | stream events as server-sent events |

Without streaming, the response comes when the answer is complete:

```json
{
  "answer": "You have 2,243 sets of identical files … [F20902] …",
  "chat_id": null,
  "model": "qwen3:14b",
  "tool_calls": [{"name": "find_duplicates", "arguments": {"limit": 10}}],
  "stats": {"output_tokens": 310, "model_calls": 2, "seconds": 21.4, "tokens_per_s": 38.2,
            "context_tokens": 6120, "model": "qwen3:14b"},
  "context": {"estimated_tokens": 5800, "prompt_tokens": 6120, "limit": 32768, "budget": 29268,
              "compacted_results": 0, "dropped_turns": 0, "remembered_docs": 3}
}
```

With `"stream": true` the response is `text/event-stream`; each event is
`data: {"type": "...", "data": ...}`:

| `type` | `data` |
|---|---|
| `context` | the `context` object above, before each model call |
| `turn_start` | start of one model call |
| `tool_call` | `{"name", "arguments"}` |
| `tool_result` | `{"name", "result"}` |
| `thinking` | reasoning text (reasoning mode only) |
| `token` | a piece of the answer |
| `turn_end` | end of one model call |
| `stats` | the `stats` object above, at the end of the answer |
| `warning` | a message, e.g. tool round limit reached |
| `error` | error text |
| `done` | `{"chat_id"}`, always last |

Closing the connection stops the answer.

## Examples

### curl

```bash
KEY=frk_xxx
BASE=http://127.0.0.1:8200/api/v1

curl -H "Authorization: Bearer $KEY" "$BASE/files?ext=pdf&name=invoice&after=2024&sort=modified"
curl -H "Authorization: Bearer $KEY" "$BASE/search?q=database%20password&category=config"
curl -H "Authorization: Bearer $KEY" "$BASE/duplicates?category=image&limit=50"
curl -H "Authorization: Bearer $KEY" -OJ "$BASE/files/F471/raw?download=1"
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"question": "Which folders take the most space?"}' "$BASE/ask"
curl -N -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"question": "Find my largest videos", "stream": true}' "$BASE/ask"
```

### Python

```python
import requests

BASE = "http://127.0.0.1:8200/api/v1"
H = {"Authorization": "Bearer frk_xxx"}

big = requests.get(f"{BASE}/files", headers=H, params={"min_size": "1GB", "sort": "size", "limit": 20}).json()
for f in big["files"]:
    print(f["size_h"], f["folder"], f["name"])

dupes = requests.get(f"{BASE}/duplicates", headers=H, params={"folder": "Photos"}).json()
print(dupes["wasted_h"], "could be freed")

# streaming answer
with requests.post(f"{BASE}/ask", headers=H, json={"question": "Where are my tax documents?", "stream": True},
                   stream=True) as r:
    for line in r.iter_lines(decode_unicode=True):
        if line.startswith("data: "):
            import json
            ev = json.loads(line[6:])
            if ev["type"] == "token":
                print(ev["data"], end="", flush=True)
```

### PowerShell

```powershell
$h = @{ Authorization = "Bearer frk_xxx" }
$base = "http://127.0.0.1:8200/api/v1"

(Invoke-RestMethod "$base/stats?by=category&sort=size" -Headers $h).rows | Format-Table label, files, size_h
(Invoke-RestMethod "$base/cleanup" -Headers $h).items | Format-Table label, files, size_h
Invoke-RestMethod "$base/roots" -Method Post -Headers $h -ContentType "application/json" `
    -Body (@{ path = "D:\Archive" } | ConvertTo-Json)
```

## Web UI endpoints

The web app itself uses unauthenticated endpoints under `/api/` (without `v1`): settings, models, chats, notes,
jobs, file reveal, and so on. They are an internal interface for the bundled UI, can change between versions, and
are reachable by anyone who can reach the server: see [Security](security.md).
