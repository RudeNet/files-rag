"""Local web UI: Flask + server-sent events over the tool-calling agent, plus the file browser."""
import json
import logging
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

from .. import __version__, config, models, notes, settings
from ..agent import Agent
from ..catalog import Filters
from ..categories import LABELS, ORDER
from ..extract import _decode
from ..llm import client
from ..retriever import Retriever
from ..scanner import Worker, add_root

STATIC = Path(__file__).parent / "static"
CHATS_DB = config.CHATS_DB

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
app.json.sort_keys = False

retriever: Retriever | None = None
worker: Worker | None = None
agents: dict[str, Agent] = {}
busy: set[str] = set()
tool_lock = threading.Lock()   # one SQLite connection is shared by all chats and the browser
_init_lock = threading.Lock()


def init():
    global retriever, worker
    with _init_lock:
        if retriever is not None:
            return
        settings.load()
        retriever = Retriever()
        worker = Worker(on_indexed=lambda: retriever.reload_vectors())
        _db().close()
        # resume work interrupted by the last shutdown
        con = retriever.db.conn
        for (rid,) in con.execute("SELECT id FROM roots WHERE status IN ('queued', 'scanning')").fetchall():
            worker.scan(rid)
        st = retriever.db.stats()
        media_todo = config.READ_MEDIA and con.execute(
            "SELECT 1 FROM files WHERE media IS NULL AND category = 'image' AND size > 0 LIMIT 1").fetchone()
        if st["pending"] or media_todo or (config.FIND_DUPLICATES and retriever.db.unhashed()) or (
                config.EMBED_CONTENT and con.execute(
                "SELECT 1 FROM chunks c LEFT JOIN vectors v ON v.chunk_id = c.id "
                "WHERE v.chunk_id IS NULL AND c.seq < ? LIMIT 1", (config.EMBED_FILE_CHUNKS,)).fetchone()):
            worker.reindex()


class ApiError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def fail(code, detail):
    raise ApiError(code, detail)


@app.errorhandler(ApiError)
def _api_error(e):
    return jsonify(detail=e.detail), e.code


@app.errorhandler(ValueError)
def _value_error(e):
    return jsonify(detail=str(e)), 400


@app.before_request
def _ensure_init():
    init()


@app.after_request
def _no_cache_static(resp):
    if not request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"   # always revalidate UI assets after edits
    return resp


def body():
    return request.get_json(silent=True) or {}


def arg_int(name, default, lo=None, hi=None):
    try:
        v = int(request.args.get(name, default))
    except (TypeError, ValueError):
        fail(400, f"{name} must be a number")
    if lo is not None and v < lo or hi is not None and v > hi:
        fail(400, f"{name} must be between {lo} and {hi}")
    return v


# ---- chat persistence ----------------------------------------------------
def _db():
    con = sqlite3.connect(CHATS_DB)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, title TEXT, "
                "created REAL, updated REAL, messages TEXT, notes TEXT DEFAULT '[]')")
    con.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, title TEXT, content TEXT, "
                "created REAL, source_chat TEXT)")
    return con


def _chat_notes(chat_row):
    """Notes attached to a chat, as [{id, title, content}] (deleted notes are skipped)."""
    ids = json.loads(chat_row["notes"] or "[]")
    if not ids:
        return []
    with _db() as con:
        rows = {r["id"]: dict(r) for r in con.execute(
            f"SELECT id, title, content FROM notes WHERE id IN ({','.join('?' * len(ids))})", ids)}
    return [rows[i] for i in ids if i in rows]


def _load_chat(chat_id):
    with _db() as con:
        r = con.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if r is None:
        fail(404, "chat not found")
    return r


def _save_chat(chat_id, messages):
    msgs = [m for m in messages if m.get("role") != "system"]
    first = next((m["content"] for m in msgs if m["role"] == "user"), "New chat")
    with _db() as con:
        con.execute("UPDATE chats SET messages = ?, updated = ?, "
                    "title = CASE WHEN title = 'New chat' THEN ? ELSE title END WHERE id = ?",
                    (json.dumps(msgs, default=str), time.time(), first[:80], chat_id))


def new_agent(**kw):
    with tool_lock:
        agent = Agent(retriever=retriever, **kw)
    agent.lock = tool_lock
    return agent


def _agent(chat_id):
    if chat_id not in agents:
        row = _load_chat(chat_id)
        agents[chat_id] = new_agent(history=json.loads(row["messages"] or "[]"), notes=_chat_notes(row))
    return agents[chat_id]


def _sse(q: queue.Queue, on_close):
    def events():
        try:
            while True:
                kind, data = q.get()
                yield f"data: {json.dumps({'type': kind, 'data': data}, default=str)}\n\n"
                if kind == "done":
                    break
        finally:
            on_close()
    return Response(events(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- pages / info ----------------------------------------------------------
@app.get("/")
def index():
    return send_file(STATIC / "index.html")


@app.get("/api/status")
def status():
    with tool_lock:
        st = retriever.db.stats()
    try:
        loaded = [{"model": m.model, "vram": m.size_vram or 0, "size": m.size or 0}
                  for m in client().ps().models]
        ollama_ok = True
    except Exception:
        loaded, ollama_ok = [], False
    return {"version": __version__, "chat_model": config.CHAT_MODEL, "embed_model": config.EMBED_MODEL,
            "ollama": ollama_ok, "loaded": loaded, "gpu": _gpu_usage(), "catalog": st,
            "vectors_loaded": len(retriever.vectors), "vectors_indexed": st["vectors"], "worker": worker.status()}


def _gpu_usage():
    """Live utilization from nvidia-smi (None if unavailable)."""
    import shutil
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
        util, used, total = (float(x) for x in out.strip().splitlines()[0].split(","))
        return {"util": util, "mem_used_mb": used, "mem_total_mb": total}
    except Exception:
        return None


def _ctx_choices(model_info):
    """Context options limited to what the model supports."""
    max_ctx = (model_info or {}).get("context_length") or max(settings.CONTEXT_CHOICES)
    return [n for n in settings.CONTEXT_CHOICES if n <= max_ctx] or [min(settings.CONTEXT_CHOICES)]


@app.get("/api/settings")
def get_settings():
    try:
        inst = models.installed()
    except Exception:
        inst = []
    chat_models = [m["name"] for m in inst if m["tools"]]
    if config.CHAT_MODEL not in chat_models:
        chat_models.insert(0, config.CHAT_MODEL)
    current = next((m for m in inst if m["name"] == config.CHAT_MODEL), None)
    return {"values": settings.current(),
            "options": {"chat_model": chat_models, "num_ctx": _ctx_choices(current)},
            "model_info": current}


INDEX_SETTINGS = {"content_index", "embed_content", "max_content_mb", "sniff_unknown"}


@app.put("/api/settings")
def put_settings():
    values = body()
    old_model = config.CHAT_MODEL
    before = settings.current()
    if "chat_model" in values:
        info = models.find(str(values["chat_model"]))
        if info is None:
            fail(400, f"{values['chat_model']} is not installed - download it first")
        if not info["tools"]:
            fail(400, f"{info['name']} does not support tool calling, which the agent needs")
        values["chat_model"] = info["name"]
        allowed = _ctx_choices(info)
        if int(values.get("num_ctx", config.NUM_CTX)) not in allowed:
            values["num_ctx"] = max(allowed)   # new model has a smaller window
    settings.apply(values)
    settings.save()
    if config.CHAT_MODEL != old_model and not busy:
        models.unload(old_model)   # free VRAM so the new model loads fully on the GPU
    changed = {k for k, v in settings.current().items() if before.get(k) != v}
    if any(getattr(config, settings.FIELDS[k][0]) for k in changed & {"embed_content", "find_duplicates",
                                                                       "read_media"}):
        worker.reindex()   # catch up on what was skipped while the option was off
    return get_settings()


# ---- models ----------------------------------------------------------------
pulling: set[str] = set()


@app.get("/api/models")
def list_models():
    try:
        inst = models.installed()
    except Exception as e:
        fail(502, f"Ollama not reachable: {e}")
    names = {m["name"] for m in inst}
    return {"installed": inst, "current": config.CHAT_MODEL, "embed_model": config.EMBED_MODEL,
            "suggested": [s for s in models.SUGGESTED if s not in names], "pulling": sorted(pulling)}


@app.post("/api/models/pull")
def pull_model():
    """Download a model (server-sent progress events). Already-installed models finish immediately."""
    name = str(body().get("name") or "").strip()
    if not models.valid_name(name):
        fail(400, "invalid model name (e.g. qwen3:8b, llama3.1:8b)")
    name = models.normalize(name)
    if name in pulling:
        fail(409, f"{name} is already downloading")
    q: queue.Queue = queue.Queue()
    cancel = threading.Event()

    def work():
        try:
            if models.find(name) is None:
                for p in models.pull(name):
                    if cancel.is_set():
                        q.put(("cancelled", None))
                        return
                    q.put(("progress", p))
            info = models.find(name)
            q.put(("error", f"{name} did not install") if info is None else ("installed", info))
        except Exception as e:
            msg = str(e)
            if "not found" in msg or "file does not exist" in msg:
                msg = f"'{name}' was not found in the Ollama library - check the name at ollama.com/library"
            q.put(("error", msg))
        finally:
            pulling.discard(name)
            q.put(("done", None))

    pulling.add(name)
    threading.Thread(target=work, daemon=True).start()
    return _sse(q, cancel.set)   # client closed the stream -> stop downloading


@app.delete("/api/models/<path:name>")
def delete_model(name):
    name = models.normalize(name)
    if name in (config.CHAT_MODEL, models.normalize(config.EMBED_MODEL)):
        fail(400, f"{name} is in use - switch to another model first")
    if models.find(name) is None:
        fail(404, f"{name} is not installed")
    models.delete(name)
    return {"ok": True}


@app.post("/api/reload-index")
def reload_index():
    return {"vectors_loaded": retriever.reload_vectors()}


# ---- mapped folders --------------------------------------------------------
@app.get("/api/overview")
def overview():
    with tool_lock:
        o = retriever.db.overview()
        roots = retriever.db.roots()
        exts = retriever.db.group("ext", None, 60)["rows"]
    return {**o, "roots_list": roots, "extensions": exts, "worker": worker.status(),
            "category_order": ORDER, "category_labels": LABELS}


@app.post("/api/roots")
def create_root():
    path = str(body().get("path") or "").strip()
    if not path:
        fail(400, "path is required")
    try:
        rid, created = add_root(path)
    except (NotADirectoryError, FileNotFoundError, OSError) as e:
        fail(404, str(e))
    worker.scan(rid)
    return {"id": rid, "created": created}


@app.post("/api/roots/<int:root_id>/scan")
def rescan_root(root_id):
    worker.scan(root_id)
    return {"queued": True}


@app.post("/api/roots/scan-all")
def rescan_all():
    with tool_lock:
        ids = [r["id"] for r in retriever.db.roots()]
    for rid in ids:
        worker.scan(rid)
    return {"queued": len(ids)}


@app.delete("/api/roots/<int:root_id>")
def delete_root(root_id):
    worker.remove(root_id)
    return {"queued": True}


@app.post("/api/jobs/stop")
def stop_jobs():
    worker.stop()
    return {"ok": True}


# ---- files -----------------------------------------------------------------
FILTER_ARGS = ("file", "name", "ext", "category", "folder", "min_size", "max_size", "after", "before", "content",
               "taken_after", "taken_before", "min_dim")


def _filters():
    f = Filters(**{k: (request.args.get(k) or "").strip() or None for k in FILTER_ARGS})
    root = request.args.get("root")
    if root and not f.folder:
        with tool_lock:
            r = next((x for x in retriever.db.roots() if str(x["id"]) == root), None)
        if r:
            f.folder = r["path"]
    return f


@app.get("/api/files")
def list_files():
    f = _filters()
    with tool_lock:
        return retriever.db.find(f, request.args.get("sort", "name"), request.args.get("order"),
                                 arg_int("limit", 100, 1, 1000), arg_int("offset", 0, 0))


@app.get("/api/search")
def search_content():
    q = (request.args.get("q") or "").strip()
    if not q:
        return {"results": []}
    mode = request.args.get("mode", "hybrid")
    if mode not in ("hybrid", "keyword", "semantic"):
        fail(400, "mode must be hybrid, keyword or semantic")
    with tool_lock:
        return {"results": retriever.search_files(q, _filters(), arg_int("limit", 40, 1, 200), mode)}


@app.get("/api/duplicates")
def duplicates():
    sort = request.args.get("sort", "wasted")
    with tool_lock:
        return retriever.db.duplicates(_filters(), sort, arg_int("limit", 20, 1, 200), arg_int("offset", 0, 0))


@app.get("/api/folders")
def folders():
    with tool_lock:
        return retriever.db.folders(request.args.get("path") or None, _filters(), arg_int("limit", 60, 1, 500))


@app.get("/api/cleanup")
def cleanup():
    with tool_lock:
        return retriever.db.cleanup()


@app.get("/api/stats")
def file_stats():
    with tool_lock:
        return retriever.db.group(request.args.get("by", "ext"), _filters(), arg_int("limit", 30, 1, 500),
                                  request.args.get("sort", "count"))


def _file(ref):
    with tool_lock:
        d = retriever.db.get_file(ref)
    if d is None:
        fail(404, f"file {ref} not found")
    return d


@app.get("/api/files/<ref>")
def get_file(ref):
    return _file(ref)


@app.get("/api/files/<ref>/read")
def read_file(ref):
    d = _file(ref)

    def opt(name):
        v = request.args.get(name)
        return int(v) if v not in (None, "") else None
    with tool_lock:
        return {"ref": d["ref"], "name": d["name"],
                **retriever.db.read(d["id"], opt("chunk") or 0, arg_int("count", 6, 1, 20), opt("page"), opt("line"))}


@app.get("/api/files/<ref>/raw")
def raw_file(ref):
    """The original file (only files in the catalog can be served)."""
    d = _file(ref)
    p = Path(d["path"])
    if not p.is_file():
        fail(404, f"the file is no longer at {p}")
    return send_file(p, as_attachment=request.args.get("download") == "1", download_name=p.name, conditional=True)


LINED_KINDS = {"text", "code", "config", "markdown"}


@app.get("/api/files/<ref>/lines")
def file_lines(ref):
    """Exact lines of a text / code file, read from disk (the chunks lose blank lines)."""
    d = _file(ref)
    if d["kind"] not in LINED_KINDS:
        fail(400, "not a plain-text file - use /read")
    p = Path(d["path"])
    if not p.is_file():
        fail(404, f"the file is no longer at {p}")
    if p.stat().st_size > config.MAX_CONTENT_MB * 2**20:
        fail(413, "file is larger than the content size limit")
    lines = _decode(p.read_bytes()).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(lines) > 1 and lines[-1] == "":   # final newline is not a line of its own
        lines.pop()
    start = arg_int("start", 1, 1)
    count = arg_int("count", 300, 1, 2000)
    return {"ref": d["ref"], "start": start, "total": len(lines), "lines": lines[start - 1:start - 1 + count],
            "more": start - 1 + count < len(lines)}


def _local_request():
    return request.remote_addr in ("127.0.0.1", "::1", "localhost")


@app.post("/api/files/<ref>/reveal")
def reveal_file(ref):
    """Show the file in Explorer / Finder - only for requests from this computer."""
    if not _local_request():
        fail(403, "only available on the computer running Files Explorer")
    p = Path(_file(ref)["path"])
    if not p.exists():
        fail(404, f"the file is no longer at {p}")
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(p)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])
    return {"ok": True}


# ---- chats -----------------------------------------------------------------
def _preview(messages_json):
    """Last visible message of a chat, for the sidebar."""
    for m in reversed(json.loads(messages_json or "[]")):
        text = (m.get("content") or "").strip()
        if m.get("role") in ("assistant", "user") and text:
            text = " ".join(text.replace("#", "").replace("*", "").replace("`", "").replace("|", " ").split())
            return ("You: " if m["role"] == "user" else "") + text[:110]
    return ""


@app.get("/api/chats")
def list_chats():
    with _db() as con:
        rows = con.execute("SELECT id, title, updated, messages FROM chats ORDER BY updated DESC").fetchall()
    return jsonify([{"id": r["id"], "title": r["title"], "updated": r["updated"], "preview": _preview(r["messages"])}
                    for r in rows])


def create_chat(note_ids=()):
    chat_id = uuid.uuid4().hex[:12]
    now = time.time()
    attached = list(note_ids)[:3]   # cap: each note costs up to ~2.5k tokens of context
    with _db() as con:
        con.execute("INSERT INTO chats (id, title, created, updated, messages, notes) "
                    "VALUES (?, 'New chat', ?, ?, '[]', ?)", (chat_id, now, now, json.dumps(attached)))
    return {"id": chat_id, "title": "New chat", "updated": now}


@app.post("/api/chats")
def post_chat():
    return create_chat(body().get("notes") or [])


@app.get("/api/chats/<chat_id>")
def get_chat(chat_id):
    r = _load_chat(chat_id)
    return {"id": r["id"], "title": r["title"], "messages": json.loads(r["messages"] or "[]"),
            "context": _agent(chat_id).context_info,
            "notes": [{"id": n["id"], "title": n["title"]} for n in _chat_notes(r)]}


def _md_download(text, title):
    name = re.sub(r"[^\w\-]+", "_", title).strip("_")[:60] or "chat"
    return Response(text, mimetype="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


@app.get("/api/chats/<chat_id>/export")
def export_chat(chat_id):
    r = _load_chat(chat_id)
    return _md_download(notes.export_markdown(r["title"], json.loads(r["messages"] or "[]"), _chat_notes(r)),
                        r["title"])


@app.post("/api/chats/<chat_id>/summarize")
def summarize_chat(chat_id):
    """Write a note summarizing the chat (runs the LLM; takes ~20-60 s)."""
    r = _load_chat(chat_id)
    messages = json.loads(r["messages"] or "[]")
    if not any(m.get("role") == "assistant" for m in messages):
        fail(400, "nothing to summarize yet")
    try:
        content = notes.summarize(r["title"], messages, _chat_notes(r))
    except Exception as e:
        fail(502, f"summary failed: {e}")
    note = {"id": uuid.uuid4().hex[:12], "title": f"Notes: {r['title']}"[:90], "content": content,
            "created": time.time(), "source_chat": chat_id}
    with _db() as con:
        con.execute("INSERT INTO notes VALUES (:id, :title, :content, :created, :source_chat)", note)
    return note


# ---- notes -----------------------------------------------------------------
@app.get("/api/notes")
def list_notes():
    with _db() as con:
        rows = con.execute("SELECT id, title, created, source_chat, length(content) AS size "
                           "FROM notes ORDER BY created DESC").fetchall()
    return jsonify([dict(r) for r in rows])


def _load_note(note_id):
    with _db() as con:
        r = con.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if r is None:
        fail(404, "note not found")
    return dict(r)


@app.get("/api/notes/<note_id>")
def get_note(note_id):
    return _load_note(note_id)


@app.get("/api/notes/<note_id>/export")
def export_note(note_id):
    n = _load_note(note_id)
    return _md_download(f"# {n['title']}\n\n{n['content']}\n", n["title"])


def _drop_agents(chat_id=None):
    """Forget cached agents (all, or one chat) so they are rebuilt with current notes; skips running ones."""
    for cid in [chat_id] if chat_id else list(agents):
        if cid not in busy:
            agents.pop(cid, None)


def _clean_note(b, partial):
    vals = {}
    if b.get("title") is not None or not partial:
        title = str(b.get("title") or "").strip()
        if not title:
            fail(400, "title is required")
        vals["title"] = title[:120]
    if b.get("content") is not None or not partial:
        content = str(b.get("content") or "").strip()
        if not content:
            fail(400, "content is required")
        if len(content) > notes.NOTE_MAX_CHARS * 2:
            fail(400, f"note too long (max {notes.NOTE_MAX_CHARS * 2:,} characters)")
        vals["content"] = content
    return vals


@app.post("/api/notes")
def create_note():
    """A hand-written note (e.g. pasted reference material)."""
    note = {"id": uuid.uuid4().hex[:12], **_clean_note(body(), partial=False), "created": time.time(),
            "source_chat": None}
    with _db() as con:
        con.execute("INSERT INTO notes VALUES (:id, :title, :content, :created, :source_chat)", note)
    return note


@app.put("/api/notes/<note_id>")
def update_note(note_id):
    _load_note(note_id)
    vals = _clean_note(body(), partial=True)
    if vals:
        with _db() as con:
            con.execute(f"UPDATE notes SET {', '.join(f'{k} = :{k}' for k in vals)} WHERE id = :id",
                        {**vals, "id": note_id})
        _drop_agents()   # chats using this note get the new text on their next message
    return _load_note(note_id)


@app.delete("/api/notes/<note_id>")
def delete_note(note_id):
    with _db() as con:
        con.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        for r in con.execute("SELECT id, notes FROM chats WHERE notes LIKE ?", (f'%"{note_id}"%',)).fetchall():
            ids = [i for i in json.loads(r["notes"] or "[]") if i != note_id]
            con.execute("UPDATE chats SET notes = ? WHERE id = ?", (json.dumps(ids), r["id"]))
    _drop_agents()
    return {"ok": True}


@app.put("/api/chats/<chat_id>/notes")
def set_chat_notes(chat_id):
    """Attach/detach notes on an existing chat (max 3)."""
    _load_chat(chat_id)
    if chat_id in busy:
        fail(409, "chat is generating")
    ids = list(dict.fromkeys(body().get("notes") or []))
    if len(ids) > 3:
        fail(400, "at most 3 notes per chat")
    for i in ids:
        _load_note(i)
    with _db() as con:
        con.execute("UPDATE chats SET notes = ? WHERE id = ?", (json.dumps(ids), chat_id))
    _drop_agents(chat_id)
    return get_chat(chat_id)


@app.delete("/api/chats/<chat_id>")
def delete_chat(chat_id):
    if chat_id in busy:
        fail(409, "chat is generating")
    agents.pop(chat_id, None)
    with _db() as con:
        con.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return {"ok": True}


@app.post("/api/chats/<chat_id>/stop")
def stop(chat_id):
    if chat_id in agents:
        agents[chat_id].cancel.set()
    return {"ok": True}


@app.post("/api/chats/<chat_id>/messages")
def send(chat_id):
    content = str(body().get("content") or "").strip()
    if not content:
        fail(400, "empty message")
    if chat_id in busy:
        fail(409, "chat is already generating")
    agent = _agent(chat_id)
    q: queue.Queue = queue.Queue()

    def work():
        try:
            agent.ask(content)
        except Exception as e:
            q.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            _save_chat(chat_id, agent.messages)
            busy.discard(chat_id)
            q.put(("done", None))

    def on_close():
        if chat_id in busy:   # client went away mid-answer
            agent.cancel.set()

    agent.on_event = lambda kind, data: q.put((kind, data))
    busy.add(chat_id)
    threading.Thread(target=work, daemon=True).start()
    return _sse(q, on_close)


BIND = {"host": "127.0.0.1", "port": 8200}   # where the server listens (shown on the API settings card)


def run(host="127.0.0.1", port=8200):
    BIND.update(host=host, port=port)
    init()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(host=host, port=port, threaded=True, use_reloader=False)


# external API + key management (see api_v1.py)
from .api_v1 import keys_admin, v1  # noqa: E402  (after app/helpers exist)

app.register_blueprint(v1)
app.register_blueprint(keys_admin)
