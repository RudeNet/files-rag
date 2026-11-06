"""Tool-calling agent loop running on a local Ollama chat model."""
import contextlib
import json
import threading
import time
from datetime import date

from . import config
from .catalog import Filters
from .categories import LABELS
from .context import FileMemory, build_view
from .llm import chat
from .notes import notes_block
from .retriever import Retriever

_FILTER_PROPS = {
    "name": {"type": "string", "description": "Words that must all appear in the file name, or a glob like "
             "'*.tar.gz' or 'invoice_*'"},
    "ext": {"type": "string", "description": "Extension(s) without dot, comma = any of, e.g. 'pdf' or 'py, js'"},
    "category": {"type": "string", "description": "File category: " + ", ".join(LABELS)},
    "folder": {"type": "string", "description": "Fragment of the folder path, e.g. 'Projects' or 'Desktop\\\\reports'"},
    "min_size": {"type": "string", "description": "Minimum size, e.g. '10MB', '500KB'"},
    "max_size": {"type": "string", "description": "Maximum size"},
    "after": {"type": "string", "description": "Modified on/after: '2024', '2024-05', '2024-05-31', or relative "
              "'7d', '3w', '6m', '1y'"},
    "before": {"type": "string", "description": "Modified before (same formats)"},
    "taken_after": {"type": "string", "description": "Photos: EXIF date taken on/after (same formats)"},
    "taken_before": {"type": "string", "description": "Photos: date taken before"},
    "min_dim": {"type": "integer", "description": "Images: longer side at least this many pixels, e.g. 1920"},
}


def _tool(name, description, props, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": list(required)}}}


TOOLS = [
    _tool("find_files",
          "Find files by metadata: name, extension, category, folder, size, modification date, and for images "
          "pixel size and date taken. Use for 'where is ...', 'list all ... files', 'largest/newest files', "
          "'files changed last week', 'photos from 2023'. Every mapped file is searchable this way, including "
          "images, videos, archives and programs.",
          {**_FILTER_PROPS,
           "sort": {"type": "string", "enum": ["name", "size", "modified", "created", "path", "ext", "taken"],
                    "description": "Sort key (default name; size/modified sort newest/largest first)"},
           "order": {"type": "string", "enum": ["asc", "desc"]},
           "limit": {"type": "integer", "description": "Files to return (default 30, max 100)"}}),
    _tool("search_content",
          "Hybrid semantic + keyword search INSIDE readable files (documents, text, code, config, e-mail, "
          "spreadsheets) and the file lists of .zip/.tar archives. Use for questions about what files say or "
          "contain, e.g. 'which file mentions the API key rotation', 'where is the function that parses dates', "
          "'which zip contains setup.py' (category='archive'). Returns passages with file ref, name, folder, and "
          "page or line.",
          {"query": {"type": "string", "description": "Natural-language or keyword query"},
           "file": {"type": "string", "description": "Restrict to files: refs like 'F3, F7' or name fragments"},
           **_FILTER_PROPS,
           "limit": {"type": "integer", "description": "Passages to return (default 8, max 25)"}}, ["query"]),
    _tool("file_stats",
          "Counts and total sizes of files grouped by ext, category, folder, root, year, month or content "
          "status, optionally filtered. Use for overviews: 'what kinds of files do I have', 'which folders use "
          "the most space', 'how many photos per year'.",
          {"group_by": {"type": "string", "enum": ["ext", "category", "folder", "root", "year", "month", "content"]},
           **_FILTER_PROPS,
           "sort": {"type": "string", "enum": ["count", "size", "name"], "description": "Default count"},
           "limit": {"type": "integer", "description": "Groups to return (default 25, max 100)"}}, ["group_by"]),
    _tool("find_duplicates",
          "Sets of identical files (same content, compared by SHA-256), largest reclaimable space first. Use for "
          "'duplicate files', 'what can I delete', 'do I have this file twice'. Takes the same filters as "
          "find_files (e.g. category='image', folder='Photos').",
          {**_FILTER_PROPS,
           "sort": {"type": "string", "enum": ["wasted", "copies", "size"],
                    "description": "wasted (default) = space freed by keeping one copy"},
           "limit": {"type": "integer", "description": "Sets to return (default 10, max 30)"}}),
    _tool("folder_sizes",
          "Sub-folders of a folder with their total size and file count, counting everything below them, "
          "largest first. Without a path: the mapped folders. Use for 'which folders take the most space', "
          "'what is in Projects', drilling down into big folders.",
          {"path": {"type": "string", "description": "Full folder path (optional)"},
           **_FILTER_PROPS,
           "limit": {"type": "integer", "description": "Sub-folders to return (default 15, max 50)"}}),
    _tool("cleanup_report",
          "Space that could be freed: duplicate copies, empty files, temp/backup files, build output, large files "
          "unchanged for a year, installers and disk images, each with the filters that list the files. Use for "
          "'how can I free space' or 'clean up my disk'. Never claim files were deleted: this app only reports.",
          {}),
    _tool("file_info",
          "Full details of one file: path, size, dates, type, whether its content is indexed, outline.",
          {"file": {"type": "string", "description": "Ref like F12, a full path, or a file name"}}, ["file"]),
    _tool("read_file",
          "Read the text of one readable file in order. Start at a line (text/code), a page (PDF, slides) or a "
          "chunk number; call again with next_chunk to continue.",
          {"file": {"type": "string", "description": "Ref like F12"},
           "line": {"type": "integer", "description": "Start near this line (optional)"},
           "page": {"type": "integer", "description": "Start at this page (optional)"},
           "from_chunk": {"type": "integer", "description": "Start at this chunk number (default 0)"},
           "chunks": {"type": "integer", "description": "How many chunks (default 4, max 12)"}}, ["file"]),
]

_ROW_KEYS = ("ref", "name", "folder", "ext", "size_h", "modified", "taken")


def _compact(row):
    out = {k: row[k] for k in _ROW_KEYS if row.get(k) not in (None, "")}
    if row.get("width"):
        out["pixels"] = f"{row['width']}x{row['height']}"
    if row.get("content") == "ready":
        out["text"] = True   # content searchable / readable
    return out


def system_prompt(db):
    o = db.overview()
    roots = db.roots()
    cats = ", ".join(f"{c['files']:,} {c['label'].lower()}" for c in sorted(o["categories"], key=lambda c: -c["files"]))
    root_lines = "\n".join(f"- {r['path']} ({r['files']:,} files, {r['size_h']})" for r in roots[:30])
    where = (f"\n\nMapped folders:\n{root_lines}" if roots else
             "\n\nNo folders are mapped yet: tell the user to add a folder on the Files page "
             "(or `python -m filerag add PATH`).")
    return f"""You are a file-search assistant over a local catalog of the user's files ({o['files']:,} files, \
{o['size_h']}: {cats or 'none'}; content of {o['readable']:,} readable files is indexed). Today is \
{date.today().isoformat()}.

Rules:
- Ground every claim about files in tool results from this conversation. Never invent file names, paths, sizes, \
dates or contents.
- Cite files inline as [F<id>] (e.g. [F12]), with a line or page when known: [F12 L40], [F12 p.3]. Only cite \
refs returned by the tools. Show the folder path when the user asks where something is.
- find_files is for names, extensions, types, folders, sizes and dates (and image size / date taken); \
search_content is for what is written inside files and inside archives; file_stats for counts by type or date; \
folder_sizes for space per folder; find_duplicates for identical copies; cleanup_report for freeing space; \
file_info and read_file for one file.
- Combine them: e.g. search_content with ext='py' for code, or find_files then read_file to check a candidate.
- If a search finds nothing, rephrase (synonyms, shorter name fragment, other extension) or relax the filters \
before concluding nothing exists. Report the total when a list is truncated.
- Present lists of files as a compact Markdown table (Ref | Name | Folder | Size | Modified) when there are \
more than three, with the ref in brackets ([F12]) so it stays clickable.
- Images are mapped with pixel size and EXIF date taken / camera; audio, video and programs by name and metadata \
only; archives by the names of the files inside them.
- Show duplicates as one table row per set (Copies | Size each | Reclaimable | Locations, citing one or two \
refs), not one row per file.
- This app never deletes or moves files. For clean-up, list candidates and let the user decide.""" + where


class Agent:
    def __init__(self, on_event=None, retriever=None, history=None, notes=None):
        self.retriever = retriever or Retriever()
        self.db = self.retriever.db
        self.on_event = on_event or (lambda kind, data: None)
        self.cancel = threading.Event()
        self.lock = contextlib.nullcontext()   # the web app shares one database connection: it sets a real lock
        self.notes = list(notes or [])
        self.reset()
        if history:  # restore a saved conversation; the system prompt is always rebuilt fresh
            self.messages = [m for m in history if m.get("role") != "system"]
            self.memory.rebuild(self.messages, clear=False)
        # computed even for an empty chat, so attached notes show up in the context meter
        self.context_info = build_view(self.messages, self.system, self.memory)[1]

    @property
    def system(self):
        # rebuilt per call: the catalog changes while chats are open
        with self.lock:
            return system_prompt(self.db) + notes_block(self.notes)

    def reset(self):
        # Full transcript (no system message). What the model sees is built per call by build_view().
        self.messages = []
        self.memory = FileMemory()
        self.memory.seed_from_notes(self.notes)
        self.context_info = {}

    # ---- tools ---------------------------------------------------------
    def _resolve(self, ref):
        f = self.db.get_file(ref)
        if f is None:
            raise LookupError(f"no file matches {ref!r} - use find_files to get refs")
        return f

    def run_tool(self, name, args):
        args = args or {}
        try:
            with self.lock:
                return self._run_tool(name, args)
        except Exception as e:  # report back to the model so it can correct itself
            return {"error": f"{type(e).__name__}: {e}"}

    def _run_tool(self, name, args):
        if name == "find_files":
            f = Filters.from_args(args)
            limit = max(1, min(int(args.get("limit") or 30), 100))
            r = self.db.find(f, args.get("sort") or "name", args.get("order"), limit)
            out = {"total": r["total"], "total_size": r["total_size_h"], "shown": len(r["files"]),
                   "files": [_compact(x) for x in r["files"]]}
            if not r["total"]:
                out["hint"] = "No files match. Try a shorter name fragment, another extension, or fewer filters."
            return out
        if name == "search_content":
            limit = max(4, min(int(args.get("limit") or 8), 25))   # models like limit=1, which starves the answer
            f = Filters.from_args(args)
            hits = self.retriever.search(args.get("query", ""), f, limit)
            if not hits and f:
                # a guessed folder/extension filter is the usual cause: retry across all files
                hits = self.retriever.search(args.get("query", ""), Filters(), limit)
                if hits:
                    return {"note": f"No matches with filters {f.describe()}; these results are from all files.",
                            "results": hits}
            if not hits:
                st = self.db.stats()
                hint = ("No content is indexed yet." if not st["chunks"] else
                        "No matches. Rephrase, use key terms, or drop filters. Try find_files for names.")
                return {"results": [], "hint": hint}
            return {"results": hits}
        if name == "file_stats":
            r = self.db.group(args.get("group_by") or "ext", Filters.from_args(args),
                              max(1, min(int(args.get("limit") or 25), 100)), args.get("sort") or "count")
            for x in r["rows"]:
                x.pop("size", None)
            return r
        if name == "find_duplicates":
            r = self.db.duplicates(Filters.from_args(args), args.get("sort") or "wasted",
                                   max(1, min(int(args.get("limit") or 10), 30)), files_per_group=10)
            out = {"sets": r["groups"], "duplicate_files": r["files"], "reclaimable": r["wasted_h"],
                   "results": [{"copies": g["copies"], "size_each": g["size_h"], "reclaimable": g["wasted_h"],
                                "files": [_compact(x) for x in g["files"]]} for g in r["results"]]}
            if r["unchecked"]:
                out["note"] = f"{r['unchecked']:,} files are still being compared; results may grow."
            return out
        if name == "folder_sizes":
            r = self.db.folders(args.get("path"), Filters.from_args(args), max(1, min(int(args.get("limit") or 15), 50)))
            return {"path": r["path"] or "(mapped folders)", "files": r["files"], "size": r["size_h"],
                    "files_directly_here": r["files_here"], "subfolders": r["folders"],
                    "children": [{"path": c["path"], "files": c["files"], "size": c["size_h"], "newest": c["newest"]}
                                 for c in r["children"]]}
        if name == "cleanup_report":
            return {"items": [{k: i[k] for k in ("label", "files", "size_h", "hint", "filters")}
                              for i in self.db.cleanup()["items"]]}
        if name == "file_info":
            f = self._resolve(args["file"])
            for k in ("id", "mtime", "ctime", "root_id"):
                f.pop(k, None)
            f["size"] = f.pop("size_h", None)
            if f.get("copies"):
                f["copies"] = [_compact(c) for c in f["copies"][:10]]
            return f
        if name == "read_file":
            f = self._resolve(args["file"])
            if f["content"] != "ready":
                return {"ref": f["ref"], "name": f["name"], "path": f["path"],
                        "error": f"content not indexed (status: {f['content']}) - only metadata is known"}
            def num(k):
                return int(args[k]) if args.get(k) not in (None, "") else None
            r = self.db.read(f["id"], num("from_chunk") or 0, min(num("chunks") or 4, 12), num("page"), num("line"))
            return {"ref": f["ref"], "name": f["name"], "folder": f["folder"], "path": f["path"], **r}
        return {"error": f"unknown tool {name}"}

    # ---- loop ----------------------------------------------------------
    def ask(self, question):
        self.cancel.clear()
        self._stats = {"output_tokens": 0, "gen_ns": 0, "model_calls": 0, "started": time.time()}
        self.messages.append({"role": "user", "content": question})
        content = ""
        for _ in range(config.MAX_TOOL_ROUNDS):
            content, calls = self._stream_turn()
            if self.cancel.is_set():
                self.messages.append({"role": "assistant", "content": content + " [stopped]",
                                      "stats": self._finish_stats()})
                return content
            msg = {"role": "assistant", "content": content}
            if calls:
                msg["tool_calls"] = calls
            else:
                msg["stats"] = self._finish_stats()
            self.messages.append(msg)
            if not calls:
                return content
            for call in calls:
                fn = call["function"]
                self.on_event("tool_call", fn)
                result = self.run_tool(fn["name"], fn.get("arguments"))
                self.memory.update(fn["name"], result)
                text = json.dumps(result, ensure_ascii=False, default=str)
                if len(text) > 16000:
                    text = text[:16000] + "...(truncated)"
                self.on_event("tool_result", {"name": fn["name"], "result": result})
                self.messages.append({"role": "tool", "content": text, "tool_name": fn["name"]})
        self.on_event("warning", "tool round limit reached")
        self.messages[-1]["stats"] = self._finish_stats()
        return content

    def _finish_stats(self):
        st = self._stats
        stats = {"output_tokens": st["output_tokens"], "model_calls": st["model_calls"],
                 "seconds": round(time.time() - st["started"], 1),
                 "tokens_per_s": round(st["output_tokens"] / (st["gen_ns"] / 1e9), 1) if st["gen_ns"] else None,
                 "context_tokens": self.context_info.get("prompt_tokens") or self.context_info.get("estimated_tokens"),
                 "model": config.CHAT_MODEL}
        self.on_event("stats", stats)
        return stats

    def _stream_turn(self):
        content, calls = [], []
        self.on_event("turn_start", None)
        # length instruction is read per call, so changing the setting applies to running chats immediately
        system = self.system + "\n- " + config.answer_length()[1]
        view, info = build_view(self.messages, system, self.memory)
        for chunk in chat(view, tools=TOOLS, stream=True):
            if self.cancel.is_set():
                break
            if chunk.done and getattr(chunk, "done_reason", None) == "length":
                note = ("\n\n_[Answer cut at the length limit - set Answer length to Normal or Detailed, "
                        "or ask to continue.]_")
                content.append(note)
                self.on_event("token", note)
            if chunk.done:
                if chunk.prompt_eval_count:
                    info["prompt_tokens"] = chunk.prompt_eval_count
                self._stats["output_tokens"] += chunk.eval_count or 0
                self._stats["gen_ns"] += chunk.eval_duration or 0
                self._stats["model_calls"] += 1
            msg = chunk.message
            if getattr(msg, "thinking", None):
                self.on_event("thinking", msg.thinking)
            if msg.content:
                content.append(msg.content)
                self.on_event("token", msg.content)
            for tc in msg.tool_calls or []:
                calls.append({"function": {"name": tc.function.name,
                                           "arguments": dict(tc.function.arguments or {})}})
        self.context_info = info
        self.on_event("context", info)
        self.on_event("turn_end", None)
        return "".join(content), calls

