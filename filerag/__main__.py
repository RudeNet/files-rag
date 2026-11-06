"""CLI: python -m filerag {doctor,add,roots,scan,remove,find,stats,dupes,folders,cleanup,show,search,reindex,ask,
chat,web}"""
import argparse
import json
import shutil
import subprocess
import sys
import threading

from rich.console import Console
from rich.table import Table

from . import __version__, config

console = Console()


def cmd_doctor(_):
    from .catalog import Catalog
    from .llm import client
    ok = True
    console.print(f"[bold]Files Explorer {__version__}[/]")
    st = Catalog().stats()
    console.print(f"catalog:   {config.CATALOG_DB} [green]ok[/] - {st['roots']} folders, {st['files']:,} files, "
                  f"{st['readable']:,} with content, {st['chunks']:,} chunks, {st['vectors']:,} embedded"
                  + (f", [yellow]{st['pending']:,} pending[/]" if st["pending"] else "")
                  + (f", [red]{st['errors']} unreadable[/]" if st["errors"] else ""))
    if shutil.which("nvidia-smi"):
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,driver_version",
                              "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
        console.print(f"GPU:       [green]{gpu}")
    else:
        console.print("GPU:       [yellow]nvidia-smi not found - Ollama may fall back to CPU")
    try:
        models = {m.model for m in client().list().models}
        console.print(f"Ollama:    [green]{config.OLLAMA_HOST} reachable[/] ({len(models)} models)")
    except Exception as e:
        console.print(f"Ollama:    [red]not reachable at {config.OLLAMA_HOST}: {e}")
        return 1
    for m in (config.CHAT_MODEL, config.EMBED_MODEL):
        have = m in models or f"{m}:latest" in models
        console.print(f"  {m:<28} {'[green]pulled' if have else f'[red]missing -> ollama pull {m}'}")
        ok &= have
    return 0 if ok else 1


def _run_pipeline(root_ids):
    from .scanner import embed_missing, hash_candidates, index_content, read_media, scan_root
    for rid in root_ids:
        with console.status("Scanning ...") as s:
            prog = {}
            done = threading.Event()

            def tick():
                while not done.wait(0.5):
                    s.update(f"Scanning {prog.get('root', '')}: {prog.get('seen', 0):,} files")
            threading.Thread(target=tick, daemon=True).start()
            try:
                scan_root(rid, prog, log=console.print)
            finally:
                done.set()
    if config.FIND_DUPLICATES:
        with console.status("Comparing same-size files ..."):
            n = hash_candidates({})
        console.print(f"  compared {n:,} file(s) for duplicates")
    if config.READ_MEDIA:
        with console.status("Reading image details ..."):
            n = read_media({})
        console.print(f"  read details of {n:,} image(s)")
    if config.CONTENT_INDEX:
        prog = {}
        with console.status("Reading file contents ..."):
            n = index_content(prog)
        console.print(f"  read {n:,} file(s)")
    if config.EMBED_CONTENT:
        with console.status(f"Embedding with {config.EMBED_MODEL} ..."):
            n = embed_missing({})
        console.print(f"  embedded {n:,} chunk(s)")
    from .catalog import connect
    with connect() as con:
        con.execute("UPDATE roots SET status = 'ready' WHERE status IN ('indexing', 'scanning')")


def cmd_add(a):
    from .scanner import add_root
    ids = []
    for p in a.paths:
        try:
            rid, created = add_root(p)
        except (NotADirectoryError, FileNotFoundError) as e:
            console.print(f"[red]{e}"); return 1
        console.print(("added " if created else "already mapped (rescanning) ") + p)
        ids.append(rid)
    _run_pipeline(ids)


def cmd_roots(a):
    from .catalog import Catalog
    t = Table()
    for col in ("Id", "Folder", "Files", "Size", "Content", "Last scan", "Status"):
        t.add_column(col, overflow="fold")
    for r in Catalog().roots():
        t.add_row(str(r["id"]), r["path"], f"{r['files']:,}", r["size_h"], f"{r['readable']:,}",
                  r["last_scan_h"] or "", r["status"] + (f" ({r['error']})" if r["error"] else ""))
    console.print(t)


def cmd_scan(a):
    from .catalog import Catalog
    roots = Catalog().roots()
    ids = [r["id"] for r in roots if not a.roots or str(r["id"]) in a.roots or r["path"] in a.roots]
    if not ids:
        console.print("[yellow]no mapped folders - add one with `python -m filerag add PATH`"); return 1
    _run_pipeline(ids)


def cmd_remove(a):
    from .catalog import Catalog
    from .scanner import remove_root
    for r in Catalog().roots():
        if str(r["id"]) in a.roots or r["path"] in a.roots:
            n = remove_root(r["id"])
            console.print(f"removed {r['path']} ({n:,} files forgotten; nothing deleted on disk)")


def _filters(a):
    from .catalog import Filters
    return Filters(name=a.name, ext=a.ext, category=a.category, folder=a.folder, min_size=a.min_size,
                   max_size=a.max_size, after=a.after, before=a.before)


def _add_filter_args(s):
    s.add_argument("-e", "--ext", help="extension(s), e.g. pdf or 'py,js'")
    s.add_argument("-t", "--category", help="document, code, image, video, archive, ...")
    s.add_argument("-f", "--folder", help="fragment of the folder path")
    s.add_argument("--min-size", help="e.g. 10MB")
    s.add_argument("--max-size")
    s.add_argument("--after", help="modified on/after: 2024, 2024-05, 2024-05-31, 7d, 6m")
    s.add_argument("--before")


def cmd_find(a):
    from .catalog import Catalog
    r = Catalog().find(_filters(a), a.sort, a.order, a.limit)
    if a.json:
        print(json.dumps(r, indent=2, default=str)); return
    t = Table(title=f"{r['total']:,} files, {r['total_size_h']}")
    for col in ("Ref", "Name", "Folder", "Size", "Modified"):
        t.add_column(col, overflow="fold")
    for f in r["files"]:
        t.add_row(f["ref"], f["name"], f["folder"], f["size_h"], f["modified"])
    console.print(t)


def cmd_stats(a):
    from .catalog import Catalog
    r = Catalog().group(a.by, _filters(a), a.limit, a.sort)
    if a.json:
        print(json.dumps(r, indent=2, default=str)); return
    t = Table(title=f"by {a.by} ({r['groups']} groups)")
    for col in (a.by, "Files", "Size", "Newest"):
        t.add_column(col, overflow="fold")
    for x in r["rows"]:
        t.add_row(str(x.get("label") or x[a.by]), f"{x['files']:,}", x["size_h"], x["newest"] or "")
    console.print(t)


def cmd_dupes(a):
    from .catalog import Catalog
    r = Catalog().duplicates(_filters(a), a.sort, a.limit)
    if a.json:
        print(json.dumps(r, indent=2, default=str)); return
    console.print(f"[bold]{r['groups']:,} sets of identical files[/], {r['files']:,} files, "
                  f"{r['wasted_h']} reclaimable" + (f" [yellow]({r['unchecked']:,} still being compared)"
                                                     if r["unchecked"] else ""))
    for g in r["results"]:
        console.print(f"\n[cyan]{g['copies']} x {g['size_h']}[/]  ({g['wasted_h']} reclaimable)")
        for f in g["files"]:
            console.print(f"  {f['ref']:<8} {f['folder']}\\{f['name']}  [dim]{f['modified']}[/]", highlight=False)


def cmd_folders(a):
    from .catalog import Catalog
    r = Catalog().folders(a.path, _filters(a), a.limit)
    if a.json:
        print(json.dumps(r, indent=2, default=str)); return
    t = Table(title=f"{r['path'] or 'mapped folders'}: {r['size_h']} in {r['files']:,} files")
    for col in ("Folder", "Size", "Files", "Newest"):
        t.add_column(col, overflow="fold")
    for c in r["children"]:
        t.add_row(c["path"], c["size_h"], f"{c['files']:,}", c["newest"] or "")
    console.print(t)


def cmd_cleanup(a):
    from .catalog import Catalog
    t = Table(title="Space that could be freed (nothing is deleted)")
    for col in ("What", "Files", "Size", "Note"):
        t.add_column(col, overflow="fold")
    for i in Catalog().cleanup()["items"]:
        t.add_row(i["label"], f"{i['files']:,}", i["size_h"], i["hint"])
    console.print(t)


def cmd_show(a):
    from .catalog import Catalog
    d = Catalog().get_file(a.file)
    if d is None:
        console.print(f"[red]{a.file} not found"); return 1
    if a.json:
        print(json.dumps(d, indent=2, default=str)); return
    console.print(f"[bold]{d['ref']} {d['name']}[/]  ({d['category_label']}, {d['size_h']}, modified {d['modified']})")
    console.print(f"[dim]{d['path']}[/]")
    console.print(f"content: {d['content']}" + (f", {d['chunks']} chunks" if d["chunks"] else "")
                  + (f" ({d['error']})" if d.get("error") else ""))


def cmd_search(a):
    from .retriever import Retriever
    hits = Retriever().search(a.query, _filters(a), a.limit, a.mode)
    if a.json:
        print(json.dumps(hits, indent=2)); return
    t = Table(show_lines=True)
    for col in ("Ref", "At", "File", "Passage"):
        t.add_column(col, overflow="fold")
    for h in hits:
        at = f"L{h['line']}" if h.get("line") else (f"p.{h['page']}" if h.get("page") else "")
        t.add_row(h["ref"], at, f"{h['name']}\n[dim]{h['folder']}[/]", h["text"][:300])
    console.print(t)


def cmd_reindex(a):
    """Re-read and re-embed every readable file (after changing the embedding model or chunk size)."""
    from .catalog import connect
    with connect() as con:
        con.execute("DELETE FROM meta WHERE k = 'embed_model'")
        con.execute("DELETE FROM vectors")
        con.execute("UPDATE files SET content = 'pending' WHERE content IN ('ready', 'error')")
    _run_pipeline([])


def _event_printer(verbose):
    state = {"streaming": False}

    def on_event(kind, data):
        if kind == "tool_call":
            console.print(f"[cyan]> {data['name']}[/] {json.dumps(data.get('arguments') or {})}")
        elif kind == "tool_result" and verbose:
            console.print(f"[dim]{json.dumps(data['result'], default=str)[:1500]}[/]")
        elif kind == "thinking" and verbose:
            console.print(data, end="", style="dim italic", markup=False, highlight=False)
        elif kind == "token":
            state["streaming"] = True
            console.print(data, end="", markup=False, highlight=False)
        elif kind == "turn_end" and state["streaming"]:
            console.print(); state["streaming"] = False
        elif kind == "warning":
            console.print(f"[yellow]{data}")

    return on_event


def _make_agent(verbose):
    from .agent import Agent
    return Agent(_event_printer(verbose))


def cmd_ask(a):
    _make_agent(a.verbose).ask(a.question)


def cmd_chat(a):
    agent = _make_agent(a.verbose)
    st = agent.db.stats()
    console.print(f"[bold]Files agent[/] - model {config.CHAT_MODEL}, embeddings {config.EMBED_MODEL}, "
                  f"{st['files']:,} files, {len(agent.retriever.vectors):,} vectors loaded")
    console.print("[dim]/context shows memory, /reset clears history, /verbose toggles tool output, /exit quits[/]")
    while True:
        try:
            q = console.input("\n[bold green]you> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q in ("/exit", "/quit"):
            break
        if q == "/context":
            console.print(agent.context_info or "[dim]empty[/]")
            for ref, item in list(agent.memory.items.items())[-15:]:
                console.print(f"  [cyan]{ref}[/] {agent.memory.line(item)}", highlight=False)
            continue
        if q == "/reset":
            agent.reset(); console.print("[dim]history cleared[/]"); continue
        if q == "/verbose":
            a.verbose = not a.verbose
            agent.on_event = _event_printer(a.verbose)
            console.print(f"[dim]verbose {'on' if a.verbose else 'off'}[/]"); continue
        try:
            agent.ask(q)
        except KeyboardInterrupt:
            console.print("\n[yellow]interrupted[/]")


def cmd_web(a):
    from .web.server import run
    console.print(f"[bold]Files Explorer {__version__}[/] - built by rudenetworks · http://{a.host}:{a.port}")
    run(a.host, a.port)


def main(argv=None):
    p = argparse.ArgumentParser(prog="filerag", description="Local AI map of your files (Ollama + CUDA)")
    p.add_argument("--version", action="version",
                   version=f"Files Explorer {__version__} - built by rudenetworks (rudenetworks.com)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check catalog, Ollama, models and GPU").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("add", help="map folders (scan, read contents, embed)")
    s.add_argument("paths", nargs="+")
    s.set_defaults(fn=cmd_add)

    sub.add_parser("roots", help="list mapped folders").set_defaults(fn=cmd_roots)

    s = sub.add_parser("scan", help="rescan mapped folders (incremental)")
    s.add_argument("roots", nargs="*", help="ids or paths (default: all)")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("remove", help="forget mapped folders (files on disk are not touched)")
    s.add_argument("roots", nargs="+", help="ids or paths")
    s.set_defaults(fn=cmd_remove)

    s = sub.add_parser("find", help="find files by name / extension / size / date")
    s.add_argument("name", nargs="?", help="words in the file name, or a glob like *.tar.gz")
    _add_filter_args(s)
    s.add_argument("--sort", default="name", choices=["name", "size", "modified", "created", "path", "ext",
                                                           "taken"])
    s.add_argument("--order", choices=["asc", "desc"])
    s.add_argument("-n", "--limit", type=int, default=50)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("stats", help="counts and sizes grouped by ext, category, folder, year, ...")
    s.add_argument("by", nargs="?", default="ext", choices=["ext", "category", "folder", "root", "year", "month",
                                                            "content"])
    s.add_argument("--name")
    _add_filter_args(s)
    s.add_argument("--sort", default="count", choices=["count", "size", "name"])
    s.add_argument("-n", "--limit", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("dupes", help="sets of identical files")
    s.add_argument("--name")
    _add_filter_args(s)
    s.add_argument("--sort", default="wasted", choices=["wasted", "copies", "size"])
    s.add_argument("-n", "--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_dupes)

    s = sub.add_parser("folders", help="folder sizes (recursive), largest first")
    s.add_argument("path", nargs="?", help="folder to look into (default: the mapped folders)")
    s.add_argument("--name")
    _add_filter_args(s)
    s.add_argument("-n", "--limit", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_folders)

    sub.add_parser("cleanup", help="space that could be freed").set_defaults(fn=cmd_cleanup)

    s = sub.add_parser("show", help="details of one file")
    s.add_argument("file", help="ref like F12, a path, or a name")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("search", help="content search, no LLM")
    s.add_argument("query")
    s.add_argument("--name")
    _add_filter_args(s)
    s.add_argument("-n", "--limit", type=int, default=10)
    s.add_argument("--mode", choices=["hybrid", "keyword", "semantic"], default="hybrid")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)

    sub.add_parser("reindex", help="re-read and re-embed all contents").set_defaults(fn=cmd_reindex)

    s = sub.add_parser("ask", help="one-shot question to the agent")
    s.add_argument("question")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("chat", help="interactive agent session")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_chat)

    s = sub.add_parser("web", help="web UI")
    s.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    s.add_argument("--port", type=int, default=8200)
    s.set_defaults(fn=cmd_web)

    a = p.parse_args(argv)
    from . import settings
    settings.load()   # same saved model/settings as the web app
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
