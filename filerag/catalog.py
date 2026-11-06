"""The file catalog: catalog.db holds mapped folders, every file in them, the text chunks of readable files,
an FTS5 index and the embeddings.

Files are referred to as F<id> (e.g. F12) everywhere: tool results, citations and the UI.
"""
import json
import os
import re
import sqlite3
from dataclasses import dataclass, fields
from datetime import datetime, timedelta

from . import config
from .categories import LABELS, ORDER, resolve

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS roots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE,            -- absolute folder path
    added REAL, last_scan REAL,
    status TEXT,                 -- queued, scanning, indexing, ready, error
    error TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id INTEGER,
    path TEXT UNIQUE,            -- absolute path
    dir TEXT,                    -- absolute parent folder
    rel TEXT,                    -- path relative to the mapped folder
    name TEXT, ext TEXT,         -- ext: lower-case, no dot, '' when none
    category TEXT,               -- document, code, image, ... (categories.py)
    size INTEGER, mtime REAL, ctime REAL,
    seen REAL,                   -- last scan that found the file
    content TEXT DEFAULT 'none', -- none (not readable), pending, ready, error, skipped (too large / disabled)
    kind TEXT, title TEXT,       -- from the extractor
    pages INTEGER, lines INTEGER, chars INTEGER, chunks INTEGER DEFAULT 0,
    indexed_mtime REAL, indexed_size INTEGER,
    sha256 TEXT,                 -- content hash of readable files and same-size duplicate candidates;
                                 -- 'distinct' / 'unreadable' are markers, not hashes
    quick TEXT,                  -- hash of the first and last 64 KB (duplicate quick check, files > 1 MB)
    width INTEGER, height INTEGER, taken REAL,   -- images: pixel size, EXIF date taken
    media TEXT,                  -- NULL = metadata not read yet, 'done'
    error TEXT, warning TEXT, meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_root ON files(root_id);
CREATE INDEX IF NOT EXISTS idx_files_ext ON files(ext);
CREATE INDEX IF NOT EXISTS idx_files_cat ON files(category);
CREATE INDEX IF NOT EXISTS idx_files_dir ON files(dir);
CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_content ON files(content);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    file_id INTEGER, seq INTEGER, page INTEGER, line INTEGER, heading TEXT, text TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id, seq);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, heading, content='chunks', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
END;
CREATE TABLE IF NOT EXISTS vectors (chunk_id INTEGER PRIMARY KEY, emb BLOB);
"""

HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.M)
FILE_REF_RE = re.compile(r"^\s*F?(\d+)\s*$", re.I)
WORD_RE = re.compile(r"[\w][\w.\-]*", re.UNICODE)
STOP = set("a an and are as at be by for from has have how i in is it of on or that the this to was were what "
           "when where which who why with does do did about into can could should would will file files".split())
SORTS = {"name": "name COLLATE NOCASE", "size": "size", "modified": "mtime", "created": "ctime",
         "path": "path COLLATE NOCASE", "ext": "ext, name COLLATE NOCASE", "taken": "taken"}
NEW_COLUMNS = [("sha256", "TEXT"), ("width", "INTEGER"), ("height", "INTEGER"), ("taken", "REAL"), ("media", "TEXT"),
               ("quick", "TEXT")]
VALID_HASH = "length(sha256) = 64"   # unreadable files get a short marker instead of a hash


def connect(readonly=False):
    if readonly:
        con = sqlite3.connect(f"file:{config.CATALOG_DB}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    else:
        con = sqlite3.connect(config.CATALOG_DB, timeout=30, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")   # readers (web app) are not blocked while scanning
        con.executescript(SCHEMA)
        have = {r[1] for r in con.execute("PRAGMA table_info(files)")}
        for col, typ in NEW_COLUMNS:   # catalogs created by earlier versions
            if col not in have:
                con.execute(f"ALTER TABLE files ADD COLUMN {col} {typ}")
        con.execute("CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_files_taken ON files(taken)")
    con.row_factory = sqlite3.Row
    return con


# ---- parsing helpers -------------------------------------------------------------------------
SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([kmgt]?)i?b?\s*$", re.I)


def parse_size(v):
    """'10MB', '500k', '1.5 GB', 2048 -> bytes."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = SIZE_RE.match(str(v))
    if not m:
        raise ValueError(f"size {v!r} not understood - use e.g. 500KB, 10MB, 2GB")
    return int(float(m.group(1)) * 1024 ** "bkmgt".index((m.group(2) or "b").lower()))


REL_RE = re.compile(r"^\s*(\d+)\s*(d|day|days|w|week|weeks|m|month|months|y|year|years|h|hour|hours)\s*(ago)?\s*$", re.I)


def parse_date(v, end=False):
    """'2024', '2024-05', '2024-05-01', '7d', '3 weeks', 'today', 'yesterday' -> unix time.

    end=True gives the end of the period (for 'before'/'until' bounds of a year or month)."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    now = datetime.now()
    if s in ("today", "yesterday"):
        d = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=s == "yesterday")
        return (d + timedelta(days=1) if end else d).timestamp()
    m = REL_RE.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2)[0]
        days = {"h": n / 24, "d": n, "w": 7 * n, "m": 30.44 * n, "y": 365.25 * n}[unit]
        return (now - timedelta(days=days)).timestamp()
    for fmt, step in (("%Y-%m-%d", "day"), ("%Y-%m", "month"), ("%Y", "year"), ("%Y/%m/%d", "day"),
                      ("%d.%m.%Y", "day"), ("%d/%m/%Y", "day")):
        try:
            d = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if end:
            d = {"day": d + timedelta(days=1),
                 "month": d.replace(year=d.year + (d.month == 12), month=d.month % 12 + 1),
                 "year": d.replace(year=d.year + 1)}[step]
        return d.timestamp()
    raise ValueError(f"date {v!r} not understood - use e.g. 2024, 2024-05, 2024-05-31 or 30d")


def _like(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def human_size(n):
    if n is None:
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def iso(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else None


# ---- filters ---------------------------------------------------------------------------------
@dataclass
class Filters:
    file: str | None = None        # F-ids or name fragments, comma = any of
    name: str | None = None        # words in the file name (all must match) or a glob like *.tar.gz / report_*
    ext: str | None = None         # 'pdf' or 'py, js, ts'
    category: str | None = None    # document, code, image, ...
    folder: str | None = None      # fragment of the folder path, e.g. 'Projects\\site' or a mapped folder
    min_size: str | None = None
    max_size: str | None = None
    after: str | None = None       # modified on/after
    before: str | None = None      # modified before
    content: str | None = None     # ready = only files whose content is indexed
    taken_after: str | None = None   # photos: EXIF date taken on/after
    taken_before: str | None = None
    min_dim: str | None = None     # images: longer side at least N pixels

    @classmethod
    def from_args(cls, a: dict):
        out = {}
        aliases = {"extension": "ext", "extensions": "ext", "type": "category", "path": "folder",
                   "directory": "folder", "modified_after": "after", "modified_before": "before",
                   "files": "file", "document": "file"}
        for k, v in (a or {}).items():
            k = aliases.get(k, k)
            if k in {f.name for f in fields(cls)} and v not in (None, ""):
                out[k] = ",".join(str(x) for x in v) if isinstance(v, list) else str(v).strip()
        return cls(**out)

    def __bool__(self):
        return any(getattr(self, f.name) for f in fields(self))

    def describe(self):
        return {f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name)}

    def sql(self, alias=""):
        """WHERE parts and args for the files table."""
        p = alias + "." if alias else ""
        where, args = [], []
        if self.file:
            ors = []
            for part in (x.strip() for x in self.file.split(",") if x.strip()):
                m = FILE_REF_RE.match(part)
                if m:
                    ors.append(f"{p}id = ?"); args.append(int(m.group(1)))
                else:
                    ors.append(f"{p}name LIKE ? ESCAPE '\\'"); args.append(f"%{_like(part)}%")
            where.append("(" + " OR ".join(ors) + ")")
        if self.name:
            n = self.name.strip()
            if "*" in n or "?" in n:
                where.append(f"{p}name LIKE ? ESCAPE '\\'")
                args.append(_like(n).replace("*", "%").replace("?", "_"))
            else:
                for w in n.split():
                    where.append(f"{p}name LIKE ? ESCAPE '\\'"); args.append(f"%{_like(w)}%")
        if self.ext:
            exts = [e.strip().lower().lstrip("*").lstrip(".") for e in re.split(r"[,\s;|]+", self.ext) if e.strip()]
            exts = [("" if e in ("none", "(none)") else e) for e in exts]
            if exts:
                where.append(f"{p}ext IN ({','.join('?' * len(exts))})"); args += exts
        if self.category:
            cats = [resolve(c) or c.strip().lower() for c in self.category.split(",") if c.strip()]
            where.append(f"{p}category IN ({','.join('?' * len(cats))})"); args += cats
        if self.folder:
            frag = self.folder.strip().strip('"').replace("/", os.sep).rstrip(os.sep)
            where.append(f"({p}dir LIKE ? ESCAPE '\\' OR {p}dir = ?)"); args += [f"%{_like(frag)}%", frag]
        if self.min_size not in (None, ""):
            where.append(f"{p}size >= ?"); args.append(parse_size(self.min_size))
        if self.max_size not in (None, ""):
            where.append(f"{p}size <= ?"); args.append(parse_size(self.max_size))
        if self.after:
            where.append(f"{p}mtime >= ?"); args.append(parse_date(self.after))
        if self.before:
            where.append(f"{p}mtime < ?"); args.append(parse_date(self.before, end=True))
        if self.content:
            where.append(f"{p}content = ?"); args.append(self.content)
        if self.taken_after:
            where.append(f"{p}taken >= ?"); args.append(parse_date(self.taken_after))
        if self.taken_before:
            where.append(f"{p}taken < ?"); args.append(parse_date(self.taken_before, end=True))
        if self.min_dim not in (None, ""):
            where.append(f"max({p}width, {p}height) >= ?"); args.append(int(str(self.min_dim).lower().rstrip("px")))
        return where, args


class Catalog:
    def __init__(self):
        connect().close()   # create the schema on first run
        self.conn = connect()

    # ---- rows ----------------------------------------------------------------------------
    @staticmethod
    def row(r, full=False):
        d = {"ref": f"F{r['id']}", "id": r["id"], "name": r["name"], "ext": r["ext"], "category": r["category"],
             "folder": r["dir"], "size": r["size"], "size_h": human_size(r["size"]), "modified": iso(r["mtime"]),
             "mtime": r["mtime"], "content": r["content"]}
        if r["width"]:
            d.update(width=r["width"], height=r["height"])
        if r["taken"]:
            d["taken"] = iso(r["taken"])
        if full:
            d.update(path=r["path"], rel=r["rel"], root_id=r["root_id"], created=iso(r["ctime"]), ctime=r["ctime"],
                     kind=r["kind"], title=r["title"], pages=r["pages"], lines=r["lines"], chars=r["chars"],
                     chunks=r["chunks"], category_label=LABELS.get(r["category"], r["category"]),
                     meta=json.loads(r["meta"] or "{}"))
        if r["warning"]:
            d["warning"] = r["warning"]
        if r["error"]:
            d["error"] = r["error"]
        return d

    # ---- find ----------------------------------------------------------------------------
    def find(self, f: Filters | None = None, sort="name", order=None, limit=50, offset=0):
        where, args = (f or Filters()).sql()
        w = (" WHERE " + " AND ".join(where)) if where else ""
        total, total_size = self.conn.execute(f"SELECT count(*), coalesce(sum(size), 0) FROM files{w}", args).fetchone()
        col = SORTS.get(sort or "name", SORTS["name"])
        desc = (order or ("desc" if sort in ("size", "modified", "created", "taken") else "asc")).lower() == "desc"
        sql = f"SELECT * FROM files{w} ORDER BY {col}{' DESC' if desc else ''} LIMIT ? OFFSET ?"
        rows = [self.row(r) for r in self.conn.execute(sql, args + [limit, offset])]
        return {"total": total, "total_size": total_size, "total_size_h": human_size(total_size), "files": rows}

    def file_ids(self, f: Filters):
        """File ids matching the filters, or None when there are no filters."""
        if not f:
            return None
        where, args = f.sql()
        return [r[0] for r in self.conn.execute(f"SELECT id FROM files WHERE {' AND '.join(where)}", args)]

    def chunk_ids(self, file_ids):
        if file_ids is None:
            return None
        if not file_ids:
            return set()
        out = set()
        for s in range(0, len(file_ids), 900):   # SQLite variable limit
            part = file_ids[s:s + 900]
            out |= {r[0] for r in self.conn.execute(
                f"SELECT id FROM chunks WHERE file_id IN ({','.join('?' * len(part))})", part)}
        return out

    # ---- statistics ----------------------------------------------------------------------
    GROUPS = {"ext": "ext", "category": "category", "folder": "dir", "root": "root_id",
              "year": "strftime('%Y', mtime, 'unixepoch', 'localtime')",
              "month": "strftime('%Y-%m', mtime, 'unixepoch', 'localtime')",
              "content": "content"}

    def group(self, by="ext", f: Filters | None = None, limit=30, sort="count"):
        expr = self.GROUPS.get(by)
        if expr is None:
            raise ValueError(f"group_by must be one of {', '.join(self.GROUPS)}")
        where, args = (f or Filters()).sql()
        w = (" WHERE " + " AND ".join(where)) if where else ""
        order = {"count": "2 DESC", "size": "3 DESC", "name": "1"}.get(sort, "2 DESC")
        rows = self.conn.execute(f"SELECT {expr} AS k, count(*), coalesce(sum(size), 0), max(mtime) FROM files{w} "
                                 f"GROUP BY k ORDER BY {order} LIMIT ?", args + [limit]).fetchall()
        roots = {r["id"]: r["path"] for r in self.conn.execute("SELECT id, path FROM roots")} if by == "root" else {}
        out = []
        for k, n, size, newest in rows:
            key = roots.get(k, k) if by == "root" else ("(no extension)" if by == "ext" and k == "" else k)
            item = {by: key, "files": n, "size": size, "size_h": human_size(size), "newest": iso(newest)}
            if by == "category":
                item["label"] = LABELS.get(k, k)
            out.append(item)
        total = self.conn.execute(f"SELECT count(DISTINCT {expr}) FROM files{w}", args).fetchone()[0]
        return {"group_by": by, "groups": total, "rows": out}

    def overview(self):
        r = self.conn.execute("SELECT count(*), coalesce(sum(size), 0), sum(content = 'ready'), "
                              "sum(content = 'pending'), sum(content = 'error'), coalesce(sum(chunks), 0) "
                              "FROM files").fetchone()
        cats = {c: {"files": n, "size": s} for c, n, s in self.conn.execute(
            "SELECT category, count(*), coalesce(sum(size), 0) FROM files GROUP BY category")}
        return {"files": r[0], "size": r[1], "size_h": human_size(r[1]), "readable": r[2] or 0,
                "pending": r[3] or 0, "errors": r[4] or 0, "chunks": r[5],
                "vectors": self.conn.execute("SELECT count(*) FROM vectors").fetchone()[0],
                "roots": self.conn.execute("SELECT count(*) FROM roots").fetchone()[0],
                "categories": [{"category": c, "label": LABELS[c], **cats[c]} for c in ORDER if c in cats]}

    def roots(self):
        out = []
        for r in self.conn.execute("SELECT * FROM roots ORDER BY path COLLATE NOCASE"):
            n, size, ready, pending = self.conn.execute(
                "SELECT count(*), coalesce(sum(size), 0), sum(content = 'ready'), sum(content = 'pending') "
                "FROM files WHERE root_id = ?", (r["id"],)).fetchone()
            out.append({"id": r["id"], "path": r["path"], "added": r["added"], "last_scan": r["last_scan"],
                        "last_scan_h": iso(r["last_scan"]), "status": r["status"], "error": r["error"],
                        "files": n, "size": size, "size_h": human_size(size), "readable": ready or 0,
                        "pending": pending or 0})
        return out

    # ---- content search ------------------------------------------------------------------
    @staticmethod
    def fts_query(text):
        words = [w for w in WORD_RE.findall(text.lower()) if w not in STOP and len(w) > 1]
        return " OR ".join('"' + w.replace('"', "") + '"' for w in dict.fromkeys(words))

    def fts_search(self, query, file_ids, k):
        q = self.fts_query(query)
        if not q:
            return []
        sql = "SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ?"
        args = [q]
        if file_ids is not None:
            if not file_ids:
                return []
            if len(file_ids) <= 900:
                sql += f" AND c.file_id IN ({','.join('?' * len(file_ids))})"
                args += file_ids
            else:   # many files: filter in Python after ranking a larger candidate set
                allowed, k2 = set(file_ids), k * 20
                rows = self.conn.execute(sql + " ORDER BY bm25(chunks_fts, 1.0, 2.0) LIMIT ?", args + [k2]).fetchall()
                fid = dict(self.conn.execute(
                    f"SELECT id, file_id FROM chunks WHERE id IN ({','.join('?' * len(rows))})", [r[0] for r in rows]
                ).fetchall()) if rows else {}
                return [r[0] for r in rows if fid.get(r[0]) in allowed][:k]
        sql += " ORDER BY bm25(chunks_fts, 1.0, 2.0) LIMIT ?"
        try:
            return [r[0] for r in self.conn.execute(sql, args + [k])]
        except sqlite3.OperationalError:
            return []

    @staticmethod
    def _focus(text, words, max_chars):
        """(offset of the first query word, window of text around it), so long chunks show the match."""
        low = text.lower()
        hits = [i for i in (low.find(w) for w in words) if i >= 0]
        idx = min(hits) if hits else 0
        if len(text) <= max_chars:
            return idx, text
        if idx < max_chars // 3:
            return idx, text[:max_chars] + "…"
        start = text.rfind("\n", 0, idx - 120) + 1 or idx - 120   # begin on the line ~120 chars before the match
        end = start + max_chars
        return idx, "…" + text[start:end] + ("…" if end < len(text) else "")

    def passages(self, chunk_ids, max_chars=700, query=None):
        if not chunk_ids:
            return []
        words = [w for w in WORD_RE.findall((query or "").lower()) if w not in STOP and len(w) > 2]
        rows = {r["id"]: r for r in self.conn.execute(
            f"SELECT c.id, c.file_id, c.seq, c.page, c.line, c.heading, c.text, f.name, f.dir, f.ext, f.category, f.pages "
            f"FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id IN ({','.join('?' * len(chunk_ids))})",
            list(chunk_ids))}
        out = []
        for cid in chunk_ids:
            r = rows.get(cid)
            if r is None:
                continue
            idx, text = self._focus(r["text"], words, max_chars)
            p = {"ref": f"F{r['file_id']}", "file_id": r["file_id"], "name": r["name"], "folder": r["dir"],
                 "ext": r["ext"], "category": r["category"]}
            if (r["pages"] or 0) > 1:   # no page key at all for page-less formats, so the model doesn't cite "p.0"
                p["page"] = r["page"]
            if r["line"]:   # line of the first match (approximate where the chunker merged blank lines)
                p["line"] = r["line"] + r["text"].count("\n", 0, idx)
            p.update(chunk=r["seq"], section=r["heading"], text=text)
            out.append(p)
        return out

    # ---- one file ------------------------------------------------------------------------
    def resolve_id(self, ref):
        m = FILE_REF_RE.match(str(ref))
        if m:
            return int(m.group(1))
        s = str(ref).strip().strip('"')
        r = self.conn.execute("SELECT id FROM files WHERE path = ? COLLATE NOCASE", (s,)).fetchone()
        if r is None:
            r = self.conn.execute("SELECT id FROM files WHERE name = ? COLLATE NOCASE ORDER BY mtime DESC LIMIT 1",
                                  (os.path.basename(s),)).fetchone()
        if r is None:
            r = self.conn.execute("SELECT id FROM files WHERE name LIKE ? ESCAPE '\\' ORDER BY mtime DESC LIMIT 1",
                                  (f"%{_like(s)}%",)).fetchone()
        return r[0] if r else None

    def get_file(self, ref):
        fid = self.resolve_id(ref)
        r = self.conn.execute("SELECT * FROM files WHERE id = ?", (fid,)).fetchone() if fid else None
        if r is None:
            return None
        d = self.row(r, full=True)
        outline, seen = [], set()
        if r["category"] not in ("code", "config"):
            for c in self.conn.execute("SELECT seq, page, heading, text FROM chunks WHERE file_id = ? ORDER BY seq",
                                       (r["id"],)):
                heads = [c["heading"]] if c["heading"] else []
                heads += [m.group(2).strip() for m in HEADING_LINE_RE.finditer(c["text"])]
                for h in heads:
                    if (h, c["page"]) not in seen:   # chunk overlap repeats headings
                        seen.add((h, c["page"]))
                        outline.append({"section": h[:200], "page": c["page"], "chunk": c["seq"]})
        d["outline"] = outline[:60]
        d["same_folder"] = self.conn.execute("SELECT count(*) FROM files WHERE dir = ?", (r["dir"],)).fetchone()[0]
        if r["sha256"] and len(r["sha256"]) == 64:
            d["copies"] = [self.row(c) for c in self.conn.execute(
                "SELECT * FROM files WHERE sha256 = ? AND id != ? ORDER BY mtime LIMIT 50", (r["sha256"], r["id"]))]
        return d

    def read(self, file_id, start=0, count=6, page=None, line=None, max_chars=12000):
        """Consecutive chunks of one file (the overlap between chunks is not removed)."""
        total = self.conn.execute("SELECT count(*) FROM chunks WHERE file_id = ?", (file_id,)).fetchone()[0]
        if page is not None:
            r = self.conn.execute("SELECT min(seq) FROM chunks WHERE file_id = ? AND page >= ?",
                                  (file_id, page)).fetchone()
            start = r[0] if r and r[0] is not None else total
        elif line is not None:
            r = self.conn.execute("SELECT max(seq) FROM chunks WHERE file_id = ? AND line <= ?",
                                  (file_id, line)).fetchone()
            start = r[0] if r and r[0] is not None else 0
        rows = self.conn.execute("SELECT seq, page, line, heading, text FROM chunks WHERE file_id = ? AND seq >= ? "
                                 "ORDER BY seq LIMIT ?", (file_id, start, max(1, min(count, 20)))).fetchall()
        out, used = [], 0
        for r in rows:
            if used and used + len(r["text"]) > max_chars:
                break
            out.append({"chunk": r["seq"], "page": r["page"], "line": r["line"], "section": r["heading"],
                        "text": r["text"]})
            used += len(r["text"])
        nxt = out[-1]["chunk"] + 1 if out else None
        return {"total_chunks": total, "chunks": out,
                "next_chunk": nxt if nxt is not None and nxt < total else None}

    # ---- duplicates ----------------------------------------------------------------------
    def duplicates(self, f: Filters | None = None, sort="wasted", limit=20, offset=0, files_per_group=20):
        """Groups of identical files (same SHA-256). Every same-size file is hashed, so this is exhaustive."""
        where, args = (f or Filters()).sql()
        w = " AND ".join([VALID_HASH, "size > 0"] + where)
        groups = f"SELECT sha256, max(size) AS size, count(*) AS n FROM files WHERE {w} GROUP BY sha256 HAVING n > 1"
        g, n, wasted = self.conn.execute(f"SELECT count(*), coalesce(sum(n), 0), coalesce(sum((n - 1) * size), 0) "
                                         f"FROM ({groups})", args).fetchone()
        order = {"wasted": "(n - 1) * size DESC", "copies": "n DESC, size DESC", "size": "size DESC"}.get(sort)
        if order is None:
            raise ValueError("sort must be wasted, copies or size")
        out = []
        for sha, size, copies in self.conn.execute(f"{groups} ORDER BY {order} LIMIT ? OFFSET ?",
                                                   args + [limit, offset]).fetchall():
            files = [self.row(r) for r in self.conn.execute(
                f"SELECT * FROM files WHERE sha256 = ? AND {w} ORDER BY mtime LIMIT ?",
                [sha] + args + [files_per_group])]
            out.append({"hash": sha[:12], "size": size, "size_h": human_size(size), "copies": copies,
                        "wasted": (copies - 1) * size, "wasted_h": human_size((copies - 1) * size), "files": files})
        return {"groups": g, "files": n, "wasted": wasted, "wasted_h": human_size(wasted),
                "unchecked": self.unhashed(), "results": out}

    def unhashed(self):
        """Same-size files whose hash is not computed yet (duplicate check still running)."""
        return self.conn.execute(
            "SELECT count(*) FROM files WHERE sha256 IS NULL AND size > 0 AND size IN "
            "(SELECT size FROM files WHERE size > 0 GROUP BY size HAVING count(*) > 1)").fetchone()[0]

    # ---- folders -------------------------------------------------------------------------
    def folders(self, path=None, f: Filters | None = None, limit=50):
        """Sub-folders of `path` (or the mapped folders) with recursive file counts and sizes."""
        where, args = (f or Filters()).sql()
        extra = (" AND " + " AND ".join(where)) if where else ""
        roots = [(r["id"], r["path"]) for r in self.conn.execute("SELECT id, path FROM roots")]
        if not path:
            children = []
            for rid, rp in roots:
                n, size, newest = self.conn.execute(
                    f"SELECT count(*), coalesce(sum(size), 0), max(mtime) FROM files WHERE root_id = ?{extra}",
                    [rid] + args).fetchone()
                children.append({"path": rp, "name": rp, "files": n, "size": size, "size_h": human_size(size),
                                 "newest": iso(newest)})
            children.sort(key=lambda c: -c["size"])
            total_n, total_size = sum(c["files"] for c in children), sum(c["size"] for c in children)
            return {"path": None, "parent": None, "files_here": 0, "size_here": 0, "files": total_n,
                    "size": total_size, "size_h": human_size(total_size), "folders": len(children),
                    "children": children[:limit]}
        path = str(path).strip().strip('"').replace("/", os.sep)
        if not (len(path) == 3 and path[1:] == ":" + os.sep):   # keep "D:\" for a drive root
            path = path.rstrip(os.sep)
        prefix = path if path.endswith(os.sep) else path + os.sep
        rows = self.conn.execute(
            f"SELECT dir, count(*), coalesce(sum(size), 0), max(mtime) FROM files "
            f"WHERE (dir = ? OR dir LIKE ? ESCAPE '\\'){extra} GROUP BY dir",
            [path, _like(prefix) + "%"] + args).fetchall()
        here_n = here_size = 0
        kids = {}
        for d, n, size, newest in rows:
            if d.lower() == path.lower():
                here_n, here_size = n, size
                continue
            name = d[len(prefix):].split(os.sep, 1)[0]
            k = kids.setdefault(name.lower(), {"path": prefix + name, "name": name, "files": 0, "size": 0,
                                               "newest": 0})
            k["files"] += n
            k["size"] += size
            k["newest"] = max(k["newest"], newest or 0)
        children = sorted(kids.values(), key=lambda c: -c["size"])
        for c in children:
            c["size_h"], c["newest"] = human_size(c["size"]), iso(c["newest"])
        total_n = here_n + sum(c["files"] for c in children)
        total_size = here_size + sum(c["size"] for c in children)
        in_root = any(path.rstrip(os.sep).lower() == rp.rstrip(os.sep).lower() for _, rp in roots)
        return {"path": path, "parent": None if in_root else os.path.dirname(path.rstrip(os.sep)),
                "files_here": here_n, "size_here": here_size, "size_here_h": human_size(here_size),
                "files": total_n, "size": total_size, "size_h": human_size(total_size), "folders": len(children),
                "children": children[:limit]}

    # ---- cleanup -------------------------------------------------------------------------
    CLEANUP = [
        # key, label, filters that list the files, hint
        ("empty", "Empty files", {"max_size": "0"}, "0-byte files, often left behind by failed downloads or tools"),
        ("temp", "Temp, backup & system files", {"category": "disk"},
         ".tmp, .bak, .old, crash dumps, partial downloads"),
        ("build", "Build output", {"category": "build"}, "compiler output that can be rebuilt (.o, .obj, .pyc, ...)"),
        ("large_old", "Large files unchanged for a year", {"min_size": "100MB", "before": "1y"},
         "100 MB or more, not modified in the last 12 months"),
        ("installers", "Installers & disk images", {"ext": "msi, iso, img, dmg, pkg, deb, rpm, appimage"},
         "often kept after installing"),
    ]

    def cleanup(self):
        """Candidates for freeing space. Nothing is deleted: each item carries the filters that list its files."""
        items = []
        for key, label, flt, hint in self.CLEANUP:
            where, args = Filters(**flt).sql()
            n, size = self.conn.execute(
                f"SELECT count(*), coalesce(sum(size), 0) FROM files WHERE {' AND '.join(where)}", args).fetchone()
            items.append({"key": key, "label": label, "files": n, "size": size, "size_h": human_size(size),
                          "filters": flt, "hint": hint})
        d = self.duplicates(limit=0)
        items.insert(0, {"key": "duplicates", "label": "Duplicate copies", "files": d["files"] - d["groups"],
                         "size": d["wasted"], "size_h": d["wasted_h"], "filters": {},
                         "hint": f"{d['groups']:,} sets of identical files; the size is what removing the extra "
                                 f"copies would free" + (f" ({d['unchecked']:,} files still being checked)"
                                                         if d["unchecked"] else "")})
        return {"items": items}

    def stats(self):
        o = self.overview()
        return {k: o[k] for k in ("files", "size", "readable", "pending", "errors", "chunks", "vectors", "roots")}
