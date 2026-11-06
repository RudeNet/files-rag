"""Conversation context management.

The full transcript is kept for storage and the UI, but what is sent to the model must fit in NUM_CTX.
Two mechanisms keep follow-up questions answerable as the chat grows:

* FileMemory - every file touched in the chat (ref, name, folder, what was done with it), injected into the
  system prompt, so "that config file" / "the second one" still resolve after old turns are trimmed.
* build_view - compacts old tool results to one-line digests, then drops the oldest turns (keeping their
  questions as a topic list), never touching the current turn's user message.
"""
import json
import re
from collections import OrderedDict

from . import config

CHARS_PER_TOKEN = 3.0      # conservative for JSON-heavy content
NOTE_FILE_RE = re.compile(r"^(?:[-*]\s*|\|\s*)\**\[?(F\d+)\]?\**\s*[:|]?(.*)$", re.I)
MEMORY_LIMIT = 80          # files listed in the memory block (most recent)
FILE_TOOLS = ("file_info", "read_file")


def estimate_tokens(messages):
    total = 0
    for m in messages:
        total += len(m.get("content") or "") + 16
        for tc in m.get("tool_calls") or []:
            total += len(json.dumps(tc.get("function", {}), default=str))
    return int(total / CHARS_PER_TOKEN)


def _parse(content):
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return None


class FileMemory:
    def __init__(self):
        self.items: OrderedDict[str, dict] = OrderedDict()   # ref -> {name, folder, note}
        self.from_notes: set[str] = set()   # known via an attached note (already in the prompt there)

    def _touch(self, ref, name=None, folder=None, note=None):
        item = self.items.pop(ref, None) or {"name": name or "?", "folder": folder or "", "note": ""}
        if name:
            item["name"] = name
        if folder:
            item["folder"] = folder
        if note:
            item["note"] = note
        self.items[ref] = item   # re-insert so most recently seen ends last
        self.from_notes.discard(ref)

    def seed_from_notes(self, notes):
        """Count files listed in attached notes as remembered (lines "- F12: ..." or table rows)."""
        for n in notes:
            for line in (n.get("content") or "").splitlines():
                m = NOTE_FILE_RE.match(line.strip())
                if m and m.group(1).upper() not in self.items:
                    self.items[m.group(1).upper()] = {"name": (m.group(2) or "").strip(" |")[:120],
                                                      "folder": "", "note": ""}
                    self.from_notes.add(m.group(1).upper())

    def update(self, tool_name, result):
        if isinstance(result, str):
            result = _parse(result)
        if tool_name in ("search_content", "find_files") and isinstance(result, dict):
            rows = result.get("results") if tool_name == "search_content" else result.get("files")
            for h in (rows or [])[:40]:
                if isinstance(h, dict) and h.get("ref"):
                    self._touch(h["ref"], h.get("name"), h.get("folder"),
                                "content matched" if tool_name == "search_content" else None)
        elif tool_name == "find_duplicates" and isinstance(result, dict):
            for g in (result.get("results") or [])[:15]:
                for h in g.get("files") or []:
                    if isinstance(h, dict) and h.get("ref"):
                        self._touch(h["ref"], h.get("name"), h.get("folder"), f"one of {g.get('copies')} copies")
        elif tool_name in FILE_TOOLS and isinstance(result, dict) and result.get("ref"):
            note = {"file_info": "details fetched", "read_file": "text read"}[tool_name]
            self._touch(result["ref"], result.get("name"), result.get("folder"), note=note)

    def rebuild(self, messages, clear=True):
        if clear:
            self.items.clear()
            self.from_notes.clear()
        for m in messages:
            if m.get("role") == "tool":
                self.update(m.get("tool_name"), m.get("content"))

    @staticmethod
    def line(item):
        bits = [item["name"]]
        if item.get("folder"):
            bits.append(item["folder"])
        if item.get("note"):
            bits.append(item["note"])
        return " | ".join(bits)

    def render(self):
        recent = [(r, i) for r, i in self.items.items() if r not in self.from_notes][-MEMORY_LIMIT:]
        if not recent:
            return ""
        lines = "\n".join(f"- {ref}: {self.line(i)}" for ref, i in recent)
        return ("\n\nFiles already seen in this conversation (oldest first). Use this to resolve follow-up "
                "references like 'that file' or 'the second one'; call the tools again when you need details:\n"
                + lines)


def digest(tool_name, content):
    """One-line summary of a tool result, used once the full result no longer fits."""
    r = _parse(content)
    if tool_name == "search_content" and isinstance(r, dict) and isinstance(r.get("results"), list):
        refs = "; ".join(f"{h['ref']} {h.get('name')}{' L' + str(h['line']) if h.get('line') else ''}: "
                         f"{(h.get('text') or '')[:50]}" for h in r["results"])
        return f"[compacted] {len(r['results'])} passages: {refs}"
    if tool_name == "find_files" and isinstance(r, dict) and isinstance(r.get("files"), list):
        return (f"[compacted] {r.get('total')} files matched; shown: "
                + ", ".join(f"{f['ref']} {f.get('name')}" for f in r["files"][:40]))
    if tool_name == "file_stats" and isinstance(r, dict) and isinstance(r.get("rows"), list):
        key = r.get("group_by")
        return "[compacted] stats by " + str(key) + ": " + ", ".join(
            f"{x.get(key)}={x.get('files')} ({x.get('size_h')})" for x in r["rows"][:25])
    if tool_name == "find_duplicates" and isinstance(r, dict) and isinstance(r.get("results"), list):
        return (f"[compacted] {r.get('sets')} duplicate sets, {r.get('reclaimable')} reclaimable; "
                + "; ".join(f"{g.get('copies')}x {g.get('size_each')}: "
                            + ", ".join(f["ref"] for f in g.get("files", [])) for g in r["results"][:15]))
    if tool_name == "folder_sizes" and isinstance(r, dict) and isinstance(r.get("children"), list):
        return (f"[compacted] {r.get('path')}: {r.get('size')} in {r.get('files')} files; "
                + ", ".join(f"{c['path']}={c['size']}" for c in r["children"][:20]))
    if tool_name == "cleanup_report" and isinstance(r, dict) and isinstance(r.get("items"), list):
        return "[compacted] cleanup: " + ", ".join(f"{i['label']} {i['files']} ({i['size_h']})" for i in r["items"])
    if tool_name in FILE_TOOLS and isinstance(r, dict) and r.get("ref"):
        return f"[compacted] {tool_name} {r['ref']} {r.get('name') or ''} {r.get('path') or ''}".strip()
    return "[compacted] " + (content or "")[:300]


def _turns(messages):
    """Split non-system messages into turns, each starting with a user message."""
    turns = []
    for m in messages:
        if m.get("role") == "system":
            continue
        if m.get("role") == "user" or not turns:
            turns.append([])
        turns[-1].append(m)
    return turns


def build_view(messages, system, memory: FileMemory, budget=None):
    """Return (messages_for_model, info) fitting within `budget` tokens."""
    budget = budget or (config.NUM_CTX - config.CONTEXT_RESERVE)
    turns = [[{k: v for k, v in m.items() if k != "stats"} for m in t] for t in _turns(messages)]
    dropped = []
    compacted = 0

    def assemble():
        sys_text = system + memory.render()
        if dropped:
            sys_text += ("\n\nEarlier questions in this conversation (full exchanges trimmed to save space):\n"
                         + "\n".join(f"- {q[:160]}" for q in dropped))
        return [{"role": "system", "content": sys_text}] + [m for t in turns for m in t]

    view = assemble()
    # 1) compact tool results, oldest turn first; the current (last) turn last of all
    for t in turns:
        if estimate_tokens(view) <= budget:
            break
        tool_msgs = [m for m in t if m["role"] == "tool" and not m["content"].startswith("[compacted]")]
        if t is turns[-1]:
            tool_msgs = tool_msgs[:-1]   # keep the freshest result of the running turn intact
        for m in tool_msgs:
            m["content"] = digest(m.get("tool_name"), m["content"])
            compacted += 1
        view = assemble()
    # 2) drop whole old turns, remembering their questions
    while estimate_tokens(view) > budget and len(turns) > 1:
        old = turns.pop(0)
        if old and old[0]["role"] == "user":
            dropped.append(old[0]["content"])
        view = assemble()

    info = {"estimated_tokens": estimate_tokens(view), "limit": config.NUM_CTX, "budget": budget,
            "compacted_results": compacted, "dropped_turns": len(dropped), "remembered_docs": len(memory.items)}
    return view, info
