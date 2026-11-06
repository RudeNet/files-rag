"""Maps folders into the catalog, in three phases so results show up early:

1. scan     - walk the folder and record every file (name, extension, category, size, dates). Fast: name /
              extension / size / date search works as soon as this is done. Vanished files are dropped.
2. hash     - SHA-256 of files that share their size with another file: finds every duplicate.
3. media    - pixel size, EXIF date taken and camera of images.
4. content  - read the text of new or changed readable files (and the file lists of archives), chunk it
              and full-text index it.
5. embed    - semantic vectors for every chunk that has none yet (skipped when embeddings are off).

Re-scanning is incremental: unchanged files (same size and mtime) are not read again.
"""
import hashlib
import json
import os
import queue
import stat as stat_mod
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import config
from .catalog import connect
from .categories import category_of, ext_of
from .extract import chunk_file, extract, is_archive, readable
from .llm import embed


def _embed_with_retry(texts):
    for attempt in range(6):   # Ollama occasionally fails transiently (e.g. port exhaustion on Windows)
        try:
            return np.asarray(embed(texts), dtype=np.float32)
        except Exception as e:
            if attempt == 5:
                raise
            print(f"\n  embed failed ({e}); retrying in {2 ** attempt}s")
            time.sleep(2 ** attempt)


def _created(st):
    return getattr(st, "st_birthtime", None) or (st.st_ctime if os.name == "nt" else st.st_mtime)


def walk(root: Path, cancel=None, errors=None):
    """Yields (path, stat) for every regular file under root, skipping excluded and (optionally) hidden folders."""
    excluded = config.exclude_dirs()
    stack = [str(root)]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError as e:
            if errors is not None:
                errors.append(f"{d}: {e.strerror or e}")
            continue
        with it:
            subdirs = []
            for e in it:
                name = e.name
                if config.SKIP_HIDDEN and name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        if name.lower() not in excluded and not _hidden_system(e):
                            subdirs.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        if name.startswith("~$"):   # Office lock files
                            continue
                        yield e.path, e.stat(follow_symlinks=False)
                except OSError:
                    continue
            stack.extend(sorted(subdirs, reverse=True))   # depth first, alphabetical


def _hidden_system(entry):
    """Windows folders flagged hidden+system (e.g. 'System Volume Information')."""
    if os.name != "nt" or not config.SKIP_HIDDEN:
        return False
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & stat_mod.FILE_ATTRIBUTE_HIDDEN and attrs & stat_mod.FILE_ATTRIBUTE_SYSTEM)


# ---- roots -----------------------------------------------------------------------------------
def add_root(path, con=None):
    """Register a folder to map. Returns (root_id, created). A folder inside an existing root is not added twice."""
    p = Path(str(path).strip().strip('"')).expanduser().resolve()
    if not p.is_dir():
        raise NotADirectoryError(f"{p} is not a folder")
    own = con is None
    con = con or connect()
    try:
        for r in con.execute("SELECT id, path FROM roots").fetchall():
            rp = Path(r["path"])
            if rp == p or rp in p.parents:
                return r["id"], False
        rid = con.execute("INSERT INTO roots (path, added, status) VALUES (?, ?, 'queued')",
                          (str(p), time.time())).lastrowid
        # an existing root inside the new one is absorbed: its files move to the new root
        for r in con.execute("SELECT id, path FROM roots WHERE id != ?", (rid,)).fetchall():
            if p in Path(r["path"]).parents:
                con.execute("UPDATE files SET root_id = ? WHERE root_id = ?", (rid, r["id"]))
                con.execute("DELETE FROM roots WHERE id = ?", (r["id"],))
        con.commit()
        return rid, True
    finally:
        if own:
            con.close()


def _delete_files(con, ids):
    for s in range(0, len(ids), 500):
        part = ids[s:s + 500]
        q = ",".join("?" * len(part))
        con.execute(f"DELETE FROM vectors WHERE chunk_id IN (SELECT id FROM chunks WHERE file_id IN ({q}))", part)
        con.execute(f"DELETE FROM chunks WHERE file_id IN ({q})", part)
        con.execute(f"DELETE FROM files WHERE id IN ({q})", part)


def remove_root(root_id, con=None):
    own = con is None
    con = con or connect()
    try:
        ids = [r[0] for r in con.execute("SELECT id FROM files WHERE root_id = ?", (root_id,))]
        _delete_files(con, ids)
        con.execute("DELETE FROM roots WHERE id = ?", (root_id,))
        con.commit()
        return len(ids)
    finally:
        if own:
            con.close()


# ---- phase 1: scan ---------------------------------------------------------------------------
def _content_state(path, size):
    if not config.CONTENT_INDEX:
        return "skipped"
    try:
        if readable(Path(path), size):
            return "pending"
    except OSError:
        return "none"
    return "skipped" if size > config.MAX_CONTENT_MB * 2**20 and readable(Path(path)) else "none"


def scan_root(root_id, progress=None, cancel=None, log=print):
    """Phase 1 for one mapped folder. Returns counts {added, changed, removed, unchanged, errors}."""
    con = connect()
    progress = progress if progress is not None else {}
    try:
        r = con.execute("SELECT path FROM roots WHERE id = ?", (root_id,)).fetchone()
        if r is None:
            return {}
        root = Path(r["path"])
        con.execute("UPDATE roots SET status = 'scanning', error = NULL WHERE id = ?", (root_id,))
        con.commit()
        if not root.is_dir():
            con.execute("UPDATE roots SET status = 'error', error = ? WHERE id = ?", ("folder not found", root_id))
            con.commit()
            return {"errors": 1}
        known = {row[0]: (row[1], row[2], row[3], row[4], row[5]) for row in con.execute(
            "SELECT path, id, size, mtime, content, category FROM files WHERE root_id = ?", (root_id,))}
        seen, errors, batch = set(), [], []
        counts = dict(added=0, changed=0, unchanged=0, removed=0)
        now = time.time()
        progress.update(phase="scan", root=str(root), seen=0)

        def flush():
            for kind, args in batch:
                if kind == "category":
                    con.execute("UPDATE files SET category = ? WHERE id = ?", args)
                elif kind == "content":
                    con.execute("UPDATE files SET content = ? WHERE id = ?", args)
                elif kind == "insert":
                    con.execute("INSERT INTO files (root_id, path, dir, rel, name, ext, category, size, mtime, ctime, "
                                "seen, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", args)
                else:
                    # changed file: hash and image metadata are recomputed
                    con.execute("UPDATE files SET size = ?, mtime = ?, ctime = ?, seen = ?, content = ?, sha256 = NULL, "
                                "quick = NULL, media = NULL, width = NULL, height = NULL, taken = NULL WHERE id = ?", args)
            con.commit()
            batch.clear()

        for path, st in walk(root, cancel, errors):
            prev = known.get(path)
            if prev is not None:
                seen.add(path)
                fid, size, mtime, content, category = prev
                if category != category_of(os.path.basename(path)):   # category map changed since the last scan
                    batch.append(("category", (category_of(os.path.basename(path)), fid)))
                if size == st.st_size and mtime == st.st_mtime:
                    counts["unchanged"] += 1
                    # settings may allow reading it now (content on, larger limit, archive listing on)
                    if content == "skipped" or (content == "none" and is_archive(Path(path))):
                        state = _content_state(path, st.st_size)
                        if state not in ("skipped", "none"):
                            batch.append(("content", (state, fid)))
                else:
                    state = _content_state(path, st.st_size)
                    batch.append(("update", (st.st_size, st.st_mtime, _created(st), now, state, fid)))
                    counts["changed"] += 1
            else:
                seen.add(path)
                name = os.path.basename(path)
                batch.append(("insert", (root_id, path, os.path.dirname(path), os.path.relpath(path, root), name,
                                         ext_of(name), category_of(name), st.st_size, st.st_mtime, _created(st),
                                         now, _content_state(path, st.st_size))))
                counts["added"] += 1
            progress["seen"] = len(seen)
            if len(batch) >= 1000:
                flush()
        flush()
        if cancel is not None and cancel.is_set():
            con.execute("UPDATE roots SET status = 'ready' WHERE id = ?", (root_id,))
            con.commit()
            return counts
        gone = [v[0] for p, v in known.items() if p not in seen]
        _delete_files(con, gone)
        counts["removed"] = len(gone)
        counts["errors"] = len(errors)
        con.execute("UPDATE roots SET status = 'indexing', last_scan = ?, error = ? WHERE id = ?",
                    (time.time(), (f"{len(errors)} folder(s) could not be read, e.g. {errors[0]}" if errors else None),
                     root_id))
        con.commit()
        log(f"  mapped {root}: " + ", ".join(f"{n} {k}" for k, n in counts.items() if n))
        return counts
    finally:
        con.close()


# ---- phase 2: duplicate candidates -----------------------------------------------------------
SAME_SIZE = "(SELECT size FROM files WHERE size > 0 GROUP BY size HAVING count(*) > 1)"
HASH_CANDIDATES = f"SELECT id, path, name, size FROM files WHERE sha256 IS NULL AND size > 0 AND size IN {SAME_SIZE}"
QUICK_BLOCK = 64 * 1024          # bytes read from each end of a file for the quick check
QUICK_MIN = 1024 * 1024          # smaller files are hashed in full straight away
# files a quick check is needed for: big same-size files next to a file that still needs a hash
QUICK_CANDIDATES = (f"SELECT id, path, name, size FROM files WHERE quick IS NULL AND size > {QUICK_MIN} "
                    "AND (sha256 IS NULL OR length(sha256) = 64) "
                    f"AND size IN {SAME_SIZE} AND size IN (SELECT size FROM files WHERE sha256 IS NULL)")
SAME_QUICK = "(SELECT size, quick FROM files WHERE quick IS NOT NULL GROUP BY size, quick HAVING count(*) > 1)"


def quick_hash(path: Path, size):
    """SHA-256 of the first and last 64 KB: files that differ there cannot be identical."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(QUICK_BLOCK))
        f.seek(max(size - QUICK_BLOCK, QUICK_BLOCK))
        h.update(f.read(QUICK_BLOCK))
    return h.hexdigest()


def _hash_rows(con, sql, compute, column, phase, progress, cancel):
    total = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
    progress.update(phase=phase, done=0, total=total)
    done = 0
    while total and not (cancel is not None and cancel.is_set()):
        rows = con.execute(sql + " ORDER BY size LIMIT 200").fetchall()
        if not rows:
            break
        for row in rows:
            if cancel is not None and cancel.is_set():
                break
            progress["current"] = row["name"]
            try:
                digest = compute(row)
            except OSError:
                digest = "unreadable"   # not a valid hash: never matches, never retried until the file changes
            con.execute(f"UPDATE files SET {column} = ? WHERE id = ?", (digest, row["id"]))
            if column == "quick" and digest == "unreadable":
                con.execute("UPDATE files SET sha256 = 'unreadable' WHERE id = ?", (row["id"],))
            done += 1
            progress["done"] = done
            if done % 100 == 0:
                con.commit()
        con.commit()
    return done


def hash_candidates(progress=None, cancel=None):
    """Find identical files while reading as little as possible. Only files that share their size with
    another file can be duplicates. Big ones first get a quick check (first and last 64 KB); only those
    whose quick check still matches another file are read in full. Returns files read."""
    if not config.FIND_DUPLICATES:
        return 0
    con = connect()
    progress = progress if progress is not None else {}
    try:
        done = _hash_rows(con, QUICK_CANDIDATES, lambda r: quick_hash(Path(r["path"]), r["size"]),
                          "quick", "quick", progress, cancel)
        if cancel is not None and cancel.is_set():
            return done
        # a new file can match one already ruled out: give that one a full hash after all
        con.execute(f"UPDATE files SET sha256 = NULL WHERE sha256 = 'distinct' AND (size, quick) IN {SAME_QUICK}")
        # big files with no quick-check match cannot be duplicates ('distinct' is not a valid hash)
        con.execute(f"UPDATE files SET sha256 = 'distinct' WHERE id IN (SELECT id FROM ({HASH_CANDIDATES})) "
                    f"AND size > {QUICK_MIN} AND quick IS NOT NULL AND (size, quick) NOT IN {SAME_QUICK}")
        con.commit()
        return done + _hash_rows(con, HASH_CANDIDATES, lambda r: sha256(Path(r["path"])), "sha256", "hash",
                                 progress, cancel)
    finally:
        progress.pop("current", None)
        con.close()


# ---- phase 3: image metadata -----------------------------------------------------------------
MEDIA_EXT = ("jpg", "jpeg", "jfif", "png", "gif", "bmp", "tif", "tiff", "webp", "ico")
EXIF_IFD, EXIF_TAKEN, EXIF_DATETIME, EXIF_MAKE, EXIF_MODEL, EXIF_GPS = 0x8769, 36867, 306, 271, 272, 34853


def image_meta(path):
    """(width, height, taken unix time or None, meta dict) from the image header and EXIF."""
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
        meta = {"Dimensions": f"{w} × {h}", "Mode": im.mode}
        exif = im.getexif()
    taken = None
    raw = exif.get_ifd(EXIF_IFD).get(EXIF_TAKEN) or exif.get(EXIF_DATETIME) if exif else None
    if raw:
        try:
            taken = datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
            meta["Taken"] = datetime.fromtimestamp(taken).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    camera = " ".join(str(exif.get(t) or "").strip("\x00 ") for t in (EXIF_MAKE, EXIF_MODEL)).strip() if exif else ""
    if camera:
        meta["Camera"] = camera
    if exif and exif.get(EXIF_GPS):
        meta["Location"] = "GPS coordinates in EXIF"
    return w, h, taken, meta


def read_media(progress=None, cancel=None):
    """Phase 3: pixel size, date taken and camera of images. Returns images read."""
    if not config.READ_MEDIA:
        return 0
    try:
        import PIL  # noqa: F401
    except ImportError:
        return 0
    con = connect()
    progress = progress if progress is not None else {}
    sql = (f"SELECT id, path, name FROM files WHERE media IS NULL AND ext IN ({','.join('?' * len(MEDIA_EXT))}) "
           "AND size > 0")
    try:
        total = con.execute(f"SELECT count(*) FROM ({sql})", MEDIA_EXT).fetchone()[0]
        progress.update(phase="media", done=0, total=total)
        done = 0
        while not (cancel is not None and cancel.is_set()):
            rows = con.execute(sql + " LIMIT 200", MEDIA_EXT).fetchall()
            if not rows:
                break
            for row in rows:
                if cancel is not None and cancel.is_set():
                    break
                progress["current"] = row["name"]
                try:
                    w, h, taken, meta = image_meta(row["path"])
                    con.execute("UPDATE files SET media = 'done', width = ?, height = ?, taken = ?, meta = ? "
                                "WHERE id = ?", (w, h, taken, json.dumps(meta), row["id"]))
                except Exception as e:   # corrupt or unsupported image: keep the file, note why
                    con.execute("UPDATE files SET media = 'done', warning = ? WHERE id = ?",
                                (f"image not readable: {type(e).__name__}"[:200], row["id"]))
                done += 1
                progress["done"] = done
                if done % 100 == 0:
                    con.commit()
            con.commit()
        return done
    finally:
        progress.pop("current", None)
        con.close()


# ---- phase 4: content ------------------------------------------------------------------------
def _clear_chunks(con, file_id):
    con.execute("DELETE FROM vectors WHERE chunk_id IN (SELECT id FROM chunks WHERE file_id = ?)", (file_id,))
    con.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))


def sha256(path: Path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _copy_from(con, file_id, donor, st, digest):
    """Identical content already indexed: copy its chunks and vectors instead of reading the file again."""
    con.execute("INSERT INTO chunks (file_id, seq, page, line, heading, text) "
                "SELECT ?, seq, page, line, heading, text FROM chunks WHERE file_id = ? ORDER BY seq",
                (file_id, donor["id"]))
    con.execute("INSERT OR IGNORE INTO vectors SELECT n.id, v.emb FROM chunks n "
                "JOIN chunks o ON o.file_id = ? AND o.seq = n.seq JOIN vectors v ON v.chunk_id = o.id "
                "WHERE n.file_id = ?", (donor["id"], file_id))
    con.execute("UPDATE files SET content = 'ready', kind = ?, title = ?, pages = ?, lines = ?, chars = ?, chunks = ?, "
                "indexed_mtime = ?, indexed_size = ?, sha256 = ?, error = NULL, warning = ?, meta = ? WHERE id = ?",
                (donor["kind"], donor["title"], donor["pages"], donor["lines"], donor["chars"], donor["chunks"],
                 st.st_mtime, st.st_size, digest, donor["warning"], donor["meta"], file_id))
    return donor["chunks"]


def index_file(con, row):
    """Read one file's text into chunks. Returns the number of chunks."""
    path = Path(row["path"])
    _clear_chunks(con, row["id"])
    try:
        st = path.stat()
        digest = row["sha256"] if row["sha256"] and len(row["sha256"]) == 64 else sha256(path)
        donor = con.execute("SELECT * FROM files WHERE sha256 = ? AND content = 'ready' AND id != ? LIMIT 1",
                            (digest, row["id"])).fetchone()
        if donor is not None:
            return _copy_from(con, row["id"], donor, st, digest)
        ex = extract(path)
        pieces = chunk_file(ex, config.CHUNK_CHARS, config.CHUNK_OVERLAP)
    except Exception as e:
        con.execute("UPDATE files SET content = 'error', error = ?, chunks = 0 WHERE id = ?",
                    (f"{type(e).__name__}: {e}"[:500], row["id"]))
        return 0
    for seq, (page, line, heading, text) in enumerate(pieces):
        con.execute("INSERT INTO chunks (file_id, seq, page, line, heading, text) VALUES (?, ?, ?, ?, ?, ?)",
                    (row["id"], seq, page, line, heading, text))
    pages = ex.meta.get("page_count") or ex.meta.get("slide_count") or (max((p for p, _ in ex.pages), default=0) or 1)
    con.execute("UPDATE files SET content = 'ready', kind = ?, title = ?, pages = ?, lines = ?, chars = ?, chunks = ?, "
                "indexed_mtime = ?, indexed_size = ?, sha256 = ?, error = NULL, warning = ?, meta = ? WHERE id = ?",
                (ex.kind, (ex.title or "").strip() or None, pages, ex.meta.get("lines"), ex.chars, len(pieces),
                 st.st_mtime, st.st_size, digest, ex.warning, json.dumps(ex.meta, default=str), row["id"]))
    return len(pieces)


def index_content(progress=None, cancel=None, root_id=None):
    """Phase 2: all pending files (optionally of one root). Returns files read."""
    con = connect()
    progress = progress if progress is not None else {}
    try:
        where = "content = 'pending'" + (" AND root_id = ?" if root_id else "")
        args = [root_id] if root_id else []
        total = con.execute(f"SELECT count(*) FROM files WHERE {where}", args).fetchone()[0]
        progress.update(phase="content", done=0, total=total)
        done = 0
        while not (cancel is not None and cancel.is_set()):
            rows = con.execute(f"SELECT id, path, name, sha256 FROM files WHERE {where} ORDER BY id LIMIT 200",
                               args).fetchall()
            if not rows:
                break
            for row in rows:
                if cancel is not None and cancel.is_set():
                    break
                progress["current"] = row["name"]
                index_file(con, row)
                done += 1
                progress["done"] = done
                if done % 50 == 0:
                    con.commit()
            con.commit()
        return done
    finally:
        progress.pop("current", None)
        con.close()


# ---- phase 5: embeddings ---------------------------------------------------------------------
def _embed_text(name, rel, heading, text):
    head = f"{name} ({rel})" + (f" > {heading}" if heading else "")
    return (config.EMBED_DOC_PREFIX + head + "\n" + text)[: config.EMBED_MAX_CHARS]


def embed_missing(progress=None, cancel=None, on_batch=None):
    """Phase 3: vectors for chunks that have none. Returns chunks embedded."""
    if not config.EMBED_CONTENT:
        return 0
    con = connect()
    progress = progress if progress is not None else {}
    try:
        row = con.execute("SELECT v FROM meta WHERE k = 'embed_model'").fetchone()
        if row and row[0] != config.EMBED_MODEL:
            raise RuntimeError(f"catalog was embedded with {row[0]} - run `python -m filerag reindex`")
        con.execute("INSERT OR IGNORE INTO meta VALUES ('embed_model', ?)", (config.EMBED_MODEL,))
        sql = ("SELECT c.id, c.heading, c.text, f.name, f.rel FROM chunks c JOIN files f ON f.id = c.file_id "
               "LEFT JOIN vectors v ON v.chunk_id = c.id WHERE v.chunk_id IS NULL AND c.seq < ?")
        cap = config.EMBED_FILE_CHUNKS
        total = con.execute(f"SELECT count(*) FROM ({sql})", (cap,)).fetchone()[0]
        progress.update(phase="embed", done=0, total=total)
        done, since_reload = 0, 0
        B = config.EMBED_BATCH
        while not (cancel is not None and cancel.is_set()):
            rows = con.execute(sql + " ORDER BY c.id LIMIT ?", (cap, B)).fetchall()
            if not rows:
                break
            vecs = _embed_with_retry([_embed_text(r["name"], os.path.dirname(r["rel"]) or ".", r["heading"], r["text"])
                                      for r in rows])
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
            con.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?)",
                            [(r["id"], v.astype(np.float16).tobytes()) for r, v in zip(rows, vecs)])
            con.commit()
            done += len(rows)
            since_reload += len(rows)
            progress["done"] = done
            if on_batch and since_reload >= 5000:   # make new vectors searchable while a big folder is embedding
                on_batch()
                since_reload = 0
        return done
    finally:
        con.close()


# ---- background worker (web app) -------------------------------------------------------------
class Worker:
    """One thread for scanning and indexing, so the disk and the GPU are not shared by several jobs."""

    def __init__(self, on_indexed=None):
        self.jobs = queue.Queue()
        self.on_indexed = on_indexed or (lambda: None)
        self.current = None
        self.progress = {}
        self.cancel = threading.Event()
        self.last_error = None
        threading.Thread(target=self._run, daemon=True).start()

    def scan(self, root_id):
        self.jobs.put(("scan", root_id))

    def remove(self, root_id):
        self.jobs.put(("remove", root_id))

    def reindex(self):
        """Hashes, image metadata, content and embeddings for anything pending (after changing settings)."""
        self.jobs.put(("index", None))

    def stop(self):
        """Stop the running job and drop queued ones (already mapped files stay)."""
        while not self.jobs.empty():
            try:
                self.jobs.get_nowait()
            except queue.Empty:
                break
        self.cancel.set()

    def pending(self):
        return self.jobs.qsize() + (1 if self.current else 0)

    def status(self):
        return {"jobs": self.pending(), "current": self.current[0] if self.current else None,
                "progress": dict(self.progress) if self.current else None, "error": self.last_error}

    def _run(self):
        log = lambda msg: None   # noqa: E731  (the UI shows status from the database)
        while True:
            job = self.jobs.get()
            self.current = job
            self.cancel.clear()
            self.progress = {}
            try:
                kind, root_id = job
                if kind == "scan":
                    scan_root(root_id, self.progress, self.cancel, log)
                if kind in ("scan", "index"):
                    hash_candidates(self.progress, self.cancel)
                    read_media(self.progress, self.cancel)
                    if config.CONTENT_INDEX:
                        index_content(self.progress, self.cancel)
                    self.on_indexed()   # keyword search on new content needs no reload, vectors do
                    embed_missing(self.progress, self.cancel, self.on_indexed)
                    self.on_indexed()
                    with connect() as con:
                        con.execute("UPDATE roots SET status = 'ready' WHERE status IN ('indexing', 'scanning')"
                                    + (" AND id = ?" if root_id else ""), (root_id,) if root_id else ())
                elif kind == "remove":
                    remove_root(root_id)
                    self.on_indexed()
                self.last_error = None
            except Exception as e:
                self.last_error = f"{job[0]} failed: {type(e).__name__}: {e}"
                print(f"background job {self.last_error}")
                with connect() as con:
                    con.execute("UPDATE roots SET status = 'error', error = ? WHERE status IN ('indexing', 'scanning')",
                                (str(e)[:500],))
            finally:
                self.current = None
                self.progress = {}
