"""External API (/api/v1) for scripts and other programs. Every call needs an API key.

Auth:  Authorization: Bearer frk_...   (or header  X-API-Key: frk_...)
Keys are stored as SHA-256 hashes in chats.db; the full key is shown only once, when created.
"""
import hashlib
import json
import queue
import secrets
import threading
import time
import uuid

from flask import Blueprint, Response, jsonify, request

from .. import __version__, config

KEY_PREFIX = "frk_"

v1 = Blueprint("v1", __name__, url_prefix="/api/v1")
keys_admin = Blueprint("keys", __name__, url_prefix="/api/keys")


def _srv():
    from . import server   # imported lazily: server imports this module
    return server


def _keys_db():
    con = _srv()._db()
    con.execute("CREATE TABLE IF NOT EXISTS api_keys (id TEXT PRIMARY KEY, name TEXT, prefix TEXT, "
                "hash TEXT UNIQUE, created REAL, last_used REAL, uses INTEGER DEFAULT 0)")
    return con


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ---- auth --------------------------------------------------------------------------------
def _auth_error(code, detail):
    resp = jsonify(detail=detail)
    resp.status_code = code
    if code == 401:
        resp.headers["WWW-Authenticate"] = "Bearer"
    return resp


@v1.before_request
def require_key():
    if not config.API_ENABLED:
        return _auth_error(403, "API access is disabled - enable it in Files Explorer Settings -> API")
    auth = request.headers.get("Authorization") or ""
    key = request.headers.get("X-API-Key") or (auth[7:].strip() if auth.lower().startswith("bearer ") else None)
    if not key:
        return _auth_error(401, "missing API key: send 'Authorization: Bearer frk_...' or 'X-API-Key: frk_...'")
    with _keys_db() as con:
        row = con.execute("SELECT id FROM api_keys WHERE hash = ?", (_hash(key),)).fetchone()
        if row is None:
            return _auth_error(401, "invalid or revoked API key")
        con.execute("UPDATE api_keys SET last_used = ?, uses = uses + 1 WHERE id = ?", (time.time(), row["id"]))
    return None


# ---- key management (used by the Settings page, same-origin UI) ---------------------------
@keys_admin.get("")
def list_keys():
    with _keys_db() as con:
        rows = con.execute("SELECT id, name, prefix, created, last_used, uses FROM api_keys "
                           "ORDER BY created DESC").fetchall()
    bind = _srv().BIND
    return {"enabled": config.API_ENABLED, "keys": [dict(r) for r in rows], "bind_host": bind["host"],
            "port": bind["port"], "local_only": bind["host"] in ("127.0.0.1", "localhost", "::1")}


@keys_admin.post("")
def create_key():
    name = str((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not 1 <= len(name) <= 60:
        _srv().fail(400, "key name must be 1-60 characters")
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    row = {"id": uuid.uuid4().hex[:12], "name": name, "prefix": key[:10], "hash": _hash(key), "created": time.time()}
    with _keys_db() as con:
        con.execute("INSERT INTO api_keys (id, name, prefix, hash, created) "
                    "VALUES (:id, :name, :prefix, :hash, :created)", row)
    return {"id": row["id"], "name": row["name"], "key": key}   # the only time the full key is returned


@keys_admin.delete("/<key_id>")
def revoke_key(key_id):
    with _keys_db() as con:
        if con.execute("DELETE FROM api_keys WHERE id = ?", (key_id,)).rowcount == 0:
            _srv().fail(404, "key not found")
    return {"ok": True}


# ---- v1 endpoints -------------------------------------------------------------------------
ENDPOINTS = [
    ("GET", "/api/v1/health", "Status, version, model, catalog size"),
    ("GET", "/api/v1/files", "Find files. Filters: name, ext, category, folder, root, min_size, max_size, after, "
                             "before; sort, order, limit, offset"),
    ("GET", "/api/v1/files/{ref}", "One file: path, size, dates, content status"),
    ("GET", "/api/v1/files/{ref}/text", "Read a file's text in order (chunk, line, page, count)"),
    ("GET", "/api/v1/files/{ref}/raw", "Download the original file"),
    ("GET", "/api/v1/search?q=...", "Content search (hybrid keyword + semantic, no LLM); same filters, mode"),
    ("GET", "/api/v1/stats?by=ext", "Counts and sizes by ext, category, folder, root, year, month, content"),
    ("GET", "/api/v1/duplicates", "Sets of identical files (same filters), sort=wasted|copies|size"),
    ("GET", "/api/v1/folders?path=...", "Sub-folders with recursive size and file count (no path: mapped folders)"),
    ("GET", "/api/v1/cleanup", "Space that could be freed: duplicates, empty, temp, build output, old large files"),
    ("GET", "/api/v1/roots", "Mapped folders"),
    ("POST", "/api/v1/roots", "Map a folder: {\"path\"}; scanned in the background"),
    ("POST", "/api/v1/ask", "Ask the agent: {\"question\", \"save\", \"chat_id\", \"stream\"}"),
]


@v1.get("")
def endpoints():
    return {"version": __version__, "endpoints": [{"method": m, "path": p, "description": d} for m, p, d in ENDPOINTS]}


@v1.get("/health")
def health():
    srv = _srv()
    with srv.tool_lock:
        st = srv.retriever.db.stats()
    return {"status": "ok", "version": __version__, "chat_model": config.CHAT_MODEL, **st,
            "vectors_loaded": len(srv.retriever.vectors), "jobs": srv.worker.pending()}


@v1.get("/files")
def files():
    return _srv().list_files()


@v1.get("/files/<ref>")
def file(ref):
    return _srv().get_file(ref)


@v1.get("/files/<ref>/text")
def file_text(ref):
    return _srv().read_file(ref)


@v1.get("/files/<ref>/raw")
def file_raw(ref):
    return _srv().raw_file(ref)


@v1.get("/search")
def search():
    srv = _srv()
    q = (request.args.get("q") or "").strip()
    if not q:
        srv.fail(400, "q is required")
    mode = request.args.get("mode", "hybrid")
    if mode not in ("hybrid", "keyword", "semantic"):
        srv.fail(400, "mode must be hybrid, keyword or semantic")
    with srv.tool_lock:
        hits = srv.retriever.search(q, srv._filters(), srv.arg_int("limit", 10, 1, 100), mode)
    return {"count": len(hits), "results": hits}


@v1.get("/stats")
def stats():
    return _srv().file_stats()


@v1.get("/duplicates")
def duplicates():
    return _srv().duplicates()


@v1.get("/folders")
def folders():
    return _srv().folders()


@v1.get("/cleanup")
def cleanup():
    return _srv().cleanup()


@v1.get("/roots")
def roots():
    srv = _srv()
    with srv.tool_lock:
        return jsonify(srv.retriever.db.roots())


@v1.post("/roots")
def add_root():
    return _srv().create_root()


@v1.post("/ask")
def ask():
    srv = _srv()
    b = request.get_json(silent=True) or {}
    question = str(b.get("question") or "").strip()
    if not 1 <= len(question) <= 20000:
        srv.fail(400, "question is required (max 20000 characters)")
    chat_id = b.get("chat_id")
    if chat_id:
        srv._load_chat(chat_id)   # 404 if unknown
    elif b.get("save"):
        chat_id = srv.create_chat()["id"]
    if chat_id and chat_id in srv.busy:
        srv.fail(409, "this chat is already answering a question")

    agent = srv._agent(chat_id) if chat_id else srv.new_agent()   # one-off question: nothing stored
    q: queue.Queue = queue.Queue()
    tools, result = [], {}

    def emit(kind, data):
        if kind == "tool_call":
            tools.append({"name": data["name"], "arguments": data.get("arguments") or {}})
        q.put((kind, data))

    def work():
        try:
            result["answer"] = agent.ask(question)
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            emit("error", result["error"])
        finally:
            if chat_id:
                srv._save_chat(chat_id, agent.messages)
                srv.busy.discard(chat_id)
            emit("done", {"chat_id": chat_id})

    agent.on_event = emit
    if chat_id:
        srv.busy.add(chat_id)
    threading.Thread(target=work, daemon=True).start()

    if b.get("stream"):
        def events():
            try:
                while True:
                    kind, data = q.get()
                    yield f"data: {json.dumps({'type': kind, 'data': data}, default=str)}\n\n"
                    if kind == "done":
                        break
            finally:
                if chat_id is None or chat_id in srv.busy:
                    agent.cancel.set()   # client disconnected mid-answer
        return Response(events(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    while q.get()[0] != "done":   # non-streaming: wait for completion
        pass
    if "error" in result:
        srv.fail(502, result["error"])
    stats_ = next((m.get("stats") for m in reversed(agent.messages) if m.get("stats")), None)
    return {"answer": result.get("answer", ""), "chat_id": chat_id, "model": config.CHAT_MODEL,
            "tool_calls": tools, "stats": stats_, "context": agent.context_info}

