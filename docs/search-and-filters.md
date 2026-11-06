# Search & filters

The same filters work everywhere: the Files page, the agent's tools, the CLI and the API. This page is the
reference for their syntax.

## Name search

| You type | Matches |
|---|---|
| `invoice` | names containing *invoice* (case-insensitive) |
| `invoice 2024` | names containing **both** *invoice* and *2024*, in any order |
| `*.tar.gz` | names ending in `.tar.gz` (a glob matches the **whole** name) |
| `report_??.xlsx` | `report_01.xlsx`, `report_q3.xlsx` (`?` = one character) |
| `IMG_*` | names starting with `IMG_` |

Name search uses the catalog only; it never opens files, so it works on every mapped file including images,
videos, archives and programs.

## Content search

Content search looks **inside** files whose text was read: documents (PDF, Word, PowerPoint, Excel, CSV, HTML,
e-mail), text and Markdown, source code, config files, Jupyter notebooks, and the file lists of archives. It
returns passages with the file, and the line (text and code) or page (PDF, slides) where the match is.

Two retrievers run and their rankings are merged (reciprocal rank fusion):

| Mode | How it matches | Good for |
|---|---|---|
| **keyword** | SQLite FTS5 / BM25 with English stemming (*running* matches *run*) and accent folding | exact names, identifiers, error messages, numbers |
| **semantic** | `nomic-embed-text` embeddings, cosine similarity | meaning: *"where do we configure retries"* finds `max_attempts = 5` |
| **hybrid** (default) | both, fused | almost everything |

The web UI and the agent always use hybrid. The CLI (`--mode`) and API (`mode=`) let you choose. If Ollama is not
running, hybrid falls back to keyword results.

What is **not** searchable by content: images (no OCR), audio, video, programs, files larger than
*Largest file to read* (20 MB by default), scanned PDFs without a text layer, old binary Office formats (`.doc`,
`.xls`, `.ppt`), `.odt`, `.rtf`, and the contents of files inside archives (only their names are listed).

Semantic vectors are made for the first 100 passages (~150 KB of text) of each file; keyword search covers the
whole file. See `FILERAG_EMBED_FILE_CHUNKS` in [Configuration](configuration.md).

## Filters

| Filter | API / CLI name | Example values | Meaning |
|---|---|---|---|
| Name | `name` / positional | `invoice 2024`, `*.log` | see [Name search](#name-search) |
| File | `file` | `F12, F40`, `config` | specific files: refs or name fragments, comma = any of (content search) |
| Extension | `ext` / `-e` | `pdf`, `py, js, ts`, `none` | extension without dot; comma = any of; `none` = files without an extension |
| Category | `category` / `-t` | `image`, `code`, `photos`, `music` | see [Categories](#categories); common words like *photos*, *movies*, *excel* work too |
| Folder | `folder` / `-f` | `Projects`, `Desktop\reports`, `D:\Work` | any part of the folder path |
| Mapped folder | `root` (API, UI) | `3` | one mapped folder, by id |
| Min size | `min_size` / `--min-size` | `500KB`, `10MB`, `1.5GB`, `2048` | at least this many bytes |
| Max size | `max_size` / `--max-size` | `0`, `100KB` | at most |
| Modified after | `after` / `--after` | see [Dates](#dates) | modified on or after |
| Modified before | `before` / `--before` | see [Dates](#dates) | modified before the end of that period |
| Taken after / before | `taken_after`, `taken_before` | `2023-06`, `2023-09` | photos: EXIF date taken |
| Min dimension | `min_dim` | `1920`, `4000` | images: longer side at least this many pixels |
| Content | `content` | `ready` | only files whose content is indexed |

All filters combine with AND.

### Sizes

A number with an optional unit: `B`, `KB`, `MB`, `GB`, `TB` (binary: 1 KB = 1024 bytes). `k`, `m`, `g` and
`MiB`-style spellings are accepted. Decimals work (`1.5GB`).

### Dates

| Form | Example | As `after` | As `before` |
|---|---|---|---|
| Year | `2024` | from 1 Jan 2024 | until the end of 2024 |
| Month | `2024-05` | from 1 May 2024 | until the end of May 2024 |
| Day | `2024-05-31`, `2024/05/31`, `31.05.2024`, `31/05/2024` | from that day | until the end of that day |
| Relative | `7d`, `3w`, `6m`, `1y`, `12h`, `3 weeks`, `2 months ago` | from that long ago | before that long ago |
| Named | `today`, `yesterday` | from midnight | until the end of the day |

So `after=2024 before=2024` means "during 2024", and `before=1y` means "not modified in the last year".

### Sorting

`sort` = `name` (default), `size`, `modified`, `created`, `path`, `ext`, `taken`. Size, dates and taken sort
largest / newest first unless `order=asc`.

## Categories

| Id | Label | Example extensions |
|---|---|---|
| `document` | Documents | pdf doc docx odt rtf epub djvu xps |
| `spreadsheet` | Spreadsheets | xls xlsx xlsm ods numbers csv tsv |
| `presentation` | Presentations | ppt pptx odp key |
| `text` | Text & notes | txt md rst log tex srt vtt |
| `code` | Source code | py js ts java c cpp cs go rs rb php sh ps1 sql html css vue … |
| `config` | Config & data | json yaml toml ini xml env plist csproj sln lock |
| `email` | E-mail | eml msg mbox |
| `image` | Images | jpg png gif bmp tiff webp heic svg psd raw cr2 nef dng |
| `audio` | Audio | mp3 wav flac aac ogg m4a opus |
| `video` | Video | mp4 mkv avi mov wmv webm |
| `archive` | Archives | zip rar 7z tar gz bz2 xz iso img dmg jar whl apk |
| `executable` | Programs | exe msi dll sys deb rpm appimage so dylib |
| `database` | Databases | db sqlite mdb accdb parquet h5 pkl npy |
| `build` | Build output | o obj pdb class pyc lib a map hex |
| `font` | Fonts | ttf otf woff woff2 |
| `design` | 3D & design | dwg dxf stl fbx blend skp step sketch fig |
| `disk` | System & temp | tmp bak old swp cache crdownload part dmp lnk |
| `other` | Other | everything else |

Some names without an extension are recognised: `Dockerfile`, `Makefile` (code), `README`, `LICENSE`,
`CHANGELOG` (text), `.gitignore`, `.env` (config). The full map is in
[`filerag/categories.py`](../filerag/categories.py).

## Image details

When *Read image details* is on, `jpg jpeg jfif png gif bmp tif tiff webp ico` files get their pixel size and,
from EXIF, the date taken, camera make and model, and whether GPS coordinates are present (coordinates themselves
are not stored). Use `min_dim`, `taken_after` / `taken_before` and `sort=taken`, or just ask the agent
(*"photos from 2023 over 4000 px"*).

## Archive listings

When *List files inside archives* is on, `.zip .jar .war .whl .apk .nupkg .epub .xpi .vsix` and
`.tar .tar.gz .tgz .tar.bz2 .tbz2 .tar.xz .txz` files are opened and the names, sizes and dates of the files inside are
indexed as the archive's content (up to 20,000 entries per archive; compressed tars up to 500 MB). Search them
with content search: *"which zip contains setup.py"*. `.7z` and `.rar` are not listed.
