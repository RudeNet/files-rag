# Security

Files Explorer indexes your files and serves them back to a browser. This page explains what that exposes and how
to run it safely.

## What the app does and does not do

| | |
|---|---|
| Reads files in mapped folders | ✅ (metadata, contents of readable files, image EXIF, archive listings) |
| Writes to mapped folders | ❌ never |
| Deletes, moves or renames files | ❌ never: clean-up and duplicates only report |
| Runs or opens your files | ❌ except *Show in folder*, which asks the OS file manager to show a file, and only for requests from the same computer |
| Network traffic | only to the Ollama server (`OLLAMA_HOST`, local by default), plus model downloads you start |
| Telemetry | none |

Copies of what it learns are stored in `catalog.db`: file paths and names, sizes and dates, **the extracted text of
readable files**, and embeddings. Treat `catalog.db` as being as sensitive as the folders you mapped. Chats in
`chats.db` can quote file contents too.

## Network exposure

The server listens on **127.0.0.1** by default: only programs on the same computer can reach it.

The web UI has **no login**. If you start it with `--host 0.0.0.0` (or `-BindHost 0.0.0.0`), anyone who can reach
the port can:

- browse, search and **download every mapped file**,
- read the extracted text of every indexed file,
- map additional folders that the server process can read (from the Files page or `POST /api/roots`),
- read and delete chats and notes, change settings, download and delete Ollama models, create API keys.

Only do this on a network you trust completely, and prefer one of these instead:

- **SSH tunnel** from the other machine: `ssh -L 8200:127.0.0.1:8200 you@server`, then open
  `http://127.0.0.1:8200` locally.
- **Reverse proxy with authentication** (Caddy, nginx, Traefik) in front of a server bound to `127.0.0.1`, with
  HTTPS and a login (basic auth, OAuth proxy, …).
- **VPN / Tailscale** so the port is only reachable by your own devices, plus a firewall rule.

## API keys

- `/api/v1` is disabled until enabled in Settings, and every request needs a key.
- Keys are 32 random bytes (`secrets.token_urlsafe`) with the prefix `frk_`; only their SHA-256 hash is stored.
  The full key is shown once.
- Revoke keys you no longer use; the list shows last use and use count.
- API keys protect **only** `/api/v1`. The UI endpoints (`/api/…`) are unauthenticated: binding to a non-local
  address exposes them regardless of API keys.

## Which folders to map

The app can only read what the user running it can read. Avoid mapping:

- folders with secrets you do not want copied into `catalog.db` (password exports, SSH keys, `.env` files with
  production credentials). `.env` and similar files *are* indexed as config files and their contents become
  searchable, which is useful locally and dangerous if the server is exposed.
- system folders or whole drives you have no need to search (slow, and broad exposure).

To keep a sub-folder out, add its name to *Settings → Mapping → Folders to skip*.

## Parsing untrusted files

Mapped folders may contain files from the internet. The extractors rely on maintained libraries (pypdf,
python-docx, python-pptx, openpyxl, Pillow, Python's `zipfile`/`tarfile`) and only read: archives are **listed,
never extracted** to disk (only their directory is read), and tar archives over 500 MB are not opened. Other files
over *Largest file to read* (20 MB) are not parsed at all. Keep the dependencies up to date (`pip install -U -r requirements.txt`).

## The model

The LLM can be wrong, and text inside your files reaches the model as tool results. A file could contain
instructions aimed at the model (prompt injection). The agent's tools are read-only, so the worst case is a
misleading answer, not an action on your system. Check important answers by clicking the citations.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
