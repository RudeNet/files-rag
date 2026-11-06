"""Chat export and summary notes.

A note is a compact Markdown brief of a conversation (goal, findings, files, open questions). Notes are
attached to new chats and injected into the system prompt, so work can continue with a fresh context.
"""
import json
from datetime import datetime

from . import config
from .context import FileMemory
from .llm import client

NOTE_MAX_CHARS = 8000          # per attached note, ~2-2.5k tokens
TRANSCRIPT_MAX_CHARS = 60000   # what the summarizer reads (fits in a 32k window)

SUMMARY_PROMPT = """You write working notes from a chat about the user's files. Produce a concise Markdown \
brief (max ~500 words) that lets a new conversation continue the work without the original transcript.

Use exactly these sections:
## Goal
## Key findings
(bullets, each keeping its file citation like [F12] or [F12 L40])
## Files used
(a Markdown table: Ref | File | Why it matters)
## Open questions / next steps

If earlier notes are provided, merge their still-relevant findings into the new brief so nothing learned \
before is lost.

Rules: only facts present in the transcript or earlier notes; keep file refs (F<id>), paths, line numbers, \
sizes, dates and names exact; skip small talk; no preamble."""


def _transcript(messages):
    parts = []
    for m in messages:
        role, text = m.get("role"), (m.get("content") or "").strip()
        if role == "user" and text:
            parts.append(f"USER: {text}")
        elif role == "assistant" and text:
            parts.append(f"ASSISTANT: {text[:3000]}")
    text = "\n\n".join(parts)
    return text[-TRANSCRIPT_MAX_CHARS:]   # keep the most recent part if very long


def summarize(title, messages, prior_notes=()):
    """LLM-written brief + a deterministic list of every file used in the chat (and its attached notes)."""
    memory = FileMemory()
    memory.seed_from_notes(prior_notes)
    memory.rebuild(messages, clear=False)
    earlier = ""
    if prior_notes:
        earlier = "Earlier notes attached to this chat:\n\n" + "\n\n".join(
            f"### {n['title']}\n{n['content'][:NOTE_MAX_CHARS]}" for n in prior_notes) + "\n\n"
    resp = client().chat(
        model=config.CHAT_MODEL, think=False, keep_alive=config.KEEP_ALIVE,
        options={**config.ollama_options(), "temperature": 0.1},
        messages=[{"role": "system", "content": SUMMARY_PROMPT},
                  {"role": "user", "content": f"Chat title: {title}\n\n{earlier}Transcript:\n\n{_transcript(messages)}"}])
    brief = resp.message.content.strip()
    files = list(memory.items.items())[-60:]
    if files:
        brief += "\n\n## All files seen in the chat\n" + "\n".join(
            f"- {ref}: {FileMemory.line(item)}" for ref, item in files)
    return brief[:NOTE_MAX_CHARS * 2]


def notes_block(notes):
    """System-prompt section for notes attached to a chat."""
    if not notes:
        return ""
    body = "\n\n".join(f"### Note: {n['title']}\n{n['content'][:NOTE_MAX_CHARS]}" for n in notes)
    return ("\n\nNotes carried over from earlier conversations (treat as established context; the user may "
            "refer to them; re-check details with tools when precision matters):\n\n" + body)


def export_markdown(title, messages, notes=()):
    out = [f"# {title}", f"_Exported {datetime.now():%Y-%m-%d %H:%M} · model {config.CHAT_MODEL}_", ""]
    for n in notes:
        out += [f"> Attached note: **{n['title']}**", ""]
    for m in messages:
        role, text = m.get("role"), (m.get("content") or "").strip()
        if role == "user":
            out += ["---", "### You", text, ""]
        elif role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                out.append(f"- _tool_ `{fn.get('name')}` {json.dumps(fn.get('arguments') or {})}")
            if text:
                out += ["", "### Assistant", text, ""]
            st = m.get("stats")
            if st:
                out += [f"_{st.get('model')} · {st.get('output_tokens')} tokens · {st.get('seconds')}s_", ""]
    return "\n".join(out)
