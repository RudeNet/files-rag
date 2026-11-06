# Changelog

All notable changes to Files Explorer. Versions follow [Semantic Versioning](https://semver.org).

## [0.1.0] - 2026-11-06

First release, built on the Document Explorer (docrag) and CVE Explorer (cve-rag) design: same agent loop,
context management, notes, settings, web UI and API, with the document library replaced by a map of the files in
folders you choose.

### Mapping
- Map any number of folders; files stay where they are. Incremental rescans: files with the same size and
  modification time are never read again; deleted files drop out of the catalog.
- Background phases, so results show up early: scan → quick check → hash → image details → contents → embeddings.
- 17 categories by extension (documents, spreadsheets, presentations, text, code, config, e-mail, images, audio,
  video, archives, programs, databases, build output, fonts, 3D & design, system & temp) plus per-extension
  statistics.
- Content extraction for PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx), CSV, HTML, e-mail (.eml), JSON,
  Jupyter notebooks and ~150 text, code and config extensions; unknown extensions are read when they look like
  text. Code and text keep exact line numbers.
- Duplicate finder: only files that share a size with another file are compared; files over 1 MB first get a quick
  check of their first and last 64 KB, and only those that still match are hashed in full (SHA-256). Identical
  files are read and embedded once.
- Image details: pixel size, EXIF date taken, camera, GPS presence (Pillow).
- Archive listings: the names inside `.zip`, `.jar`, `.war`, `.whl`, `.apk`, `.nupkg`, `.epub`, `.xpi`, `.vsix`
  and `.tar(.gz/.bz2/.xz)` are searchable.
- Skips `.git`, `node_modules`, virtual environments, hidden and system folders (configurable).

### Search and browsing
- Files page: mapped folders with live progress, folder sizes (recursive drill-down), clean-up report, breakdown
  by type and extension, and a browser with Name, Content and Duplicates modes, filters and sorting.
- Hybrid content search: SQLite FTS5/BM25 + `nomic-embed-text` embeddings, merged with reciprocal rank fusion.
- File panel: details, identical copies, image / audio / video preview, source view with line numbers, PDF/Office
  text by page, Open, Show in folder, Copy path, Download.

### Agent
- Tools: `find_files`, `search_content`, `file_stats`, `folder_sizes`, `find_duplicates`, `cleanup_report`,
  `file_info`, `read_file`.
- Inline citations `[F12]`, `[F12 L40]`, `[F12 p.3]`, clickable in the UI; file lists as tables.
- The app never deletes or moves files; clean-up answers only list candidates.

### Conversation, web app, API, CLI
- Chat history, follow-ups with remembered files, automatic compaction, context meter, Summarize & continue,
  notes attachable to chats, Markdown export, answer length setting.
- Themes (System / Light / Dark / Hack), model switching and downloading, live GPU / VRAM status.
- HTTP API (`/api/v1`) with API keys (off by default).
- CLI: `doctor`, `add`, `roots`, `scan`, `remove`, `find`, `stats`, `dupes`, `folders`, `cleanup`, `show`,
  `search`, `reindex`, `ask`, `chat`, `web`.
