(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const md = window.renderMarkdown, esc = window.escapeHtml, fileLink = window.docLink;
  const messagesEl = $("messages"), input = $("input");

  let chats = [], currentId = null, generating = false, lastContext = null;
  let catLabels = {};

  // ---------- preferences (per browser) ----------
  const PREF_DEFAULTS = { theme: "system", fontSize: "medium", enterSends: true, expandSteps: false, showStats: true };
  let prefs = { ...PREF_DEFAULTS };
  try { prefs = { ...PREF_DEFAULTS, ...JSON.parse(localStorage.getItem("filerag.prefs") || "{}") }; } catch {}
  function savePrefs() { try { localStorage.setItem("filerag.prefs", JSON.stringify(prefs)); } catch {} }
  function applyPrefs() {
    const root = document.documentElement;
    if (prefs.theme === "system") delete root.dataset.theme; else root.dataset.theme = prefs.theme;
    root.dataset.fontsize = prefs.fontSize;
    document.body.classList.toggle("no-stats", !prefs.showStats);
    $("sendHint").textContent = prefs.enterSends ? "Enter to send · Shift+Enter for new line"
                                                 : "Ctrl+Enter to send · Enter for new line";
  }

  // ---------- api ----------
  async function api(path, opts = {}) {
    const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }
  const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v != null)).toString();

  // ---------- file helpers ----------
  const fmtSize = (n) => n == null ? "" : n >= 2 ** 30 ? (n / 2 ** 30).toFixed(1) + " GB" : n >= 2 ** 20 ? (n / 2 ** 20).toFixed(1) + " MB"
    : n >= 1024 ? Math.round(n / 1024) + " KB" : n + " B";
  const fileIcon = (f, big) => `<span class="ficon${big ? " lg" : ""}" style="background:var(--cat-${esc(f.category || "other")})"` +
    ` title="${esc(catLabels[f.category] || f.category || "")}">${esc((f.ext || "—").slice(0, 4))}</span>`;
  const IMG_EXT = new Set(["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "avif", "ico", "jfif"]);
  const VIDEO_EXT = new Set(["mp4", "webm", "m4v", "ogv", "mov"]);
  const AUDIO_EXT = new Set(["mp3", "wav", "ogg", "oga", "m4a", "flac", "opus", "aac"]);
  function highlight(text, query) {
    const words = (query || "").toLowerCase().match(/[\p{L}\p{N}_.\-]{3,}/gu) || [];
    let h = esc(text);
    for (const w of [...new Set(words)].slice(0, 8)) {
      const re = new RegExp(esc(w).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
      h = h.replace(re, (m) => `<mark>${m}</mark>`);
    }
    return h;
  }

  function focusText(text, query) {   // start a snippet at the line of the first query word
    const low = text.toLowerCase();
    const idx = Math.min(...((query || "").toLowerCase().match(/[\p{L}\p{N}_.\-]{3,}/gu) || [])
      .map((w) => low.indexOf(w)).filter((i) => i >= 0), Infinity);
    if (!isFinite(idx) || idx < 80) return text;
    return "…" + text.slice(text.lastIndexOf("\n", idx - 1) + 1);
  }

  // ---------- sidebar ----------
  async function loadChats() {
    chats = await api("/api/chats");
    const list = $("chatList");
    list.innerHTML = "";
    for (const c of chats) {
      const item = el("div", "chat-item" + (c.id === currentId ? " active" : ""));
      const a = el("a"); a.href = "#" + c.id; a.title = c.title;
      a.innerHTML = `<span class="t-row"><span class="title">${esc(c.title)}</span><span class="time">${ago(c.updated)}</span></span>` +
        (c.preview ? `<span class="preview">${esc(c.preview)}</span>` : "");
      const del = el("button", "del", "✕"); del.title = "Delete chat";
      del.onclick = async (e) => {
        e.preventDefault();
        if (!confirm("Delete this chat?")) return;
        await api(`/api/chats/${c.id}`, { method: "DELETE" });
        if (c.id === currentId) newChat(); else loadChats();
      };
      item.append(a, del);
      list.append(item);
    }
  }

  async function loadNotes() {
    const list = $("noteList");
    let notesList = [];
    try { notesList = await api("/api/notes"); } catch { return; }
    list.innerHTML = notesList.length ? "" : '<p class="list-empty">No notes yet. Use “Summarize” on a chat.</p>';
    for (const n of notesList) {
      const item = el("div", "chat-item note-item");
      const a = el("a"); a.href = "#"; a.title = n.title;
      a.innerHTML = `<span class="t-row"><span class="title">${esc(n.title.replace(/^Notes: /, ""))}</span><span class="time">${ago(n.created)}</span></span>` +
        `<span class="preview">~${Math.ceil(n.size / 4).toLocaleString()} tokens</span>`;
      a.onclick = (e) => { e.preventDefault(); openNote(n.id); };
      item.append(a); list.append(item);
    }
  }

  const NOTE_LIMIT = 3;
  let currentNotes = [];   // [{id, title}] attached to the open chat

  async function setChatNotes(ids) {
    const chat = await api(`/api/chats/${currentId}/notes`, { method: "PUT", body: JSON.stringify({ notes: ids }) });
    showNoteChips(chat.notes);
    showContext(chat.context);
    return chat;
  }

  function openDrawer(wide) {
    const drawer = $("drawer");
    drawer.classList.toggle("wide", !!wide);
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
  }

  function noteEditor(n) {
    // n = existing note, or null for a new one
    const body = $("drawerBody");
    $("drawerTitle").textContent = n ? "Edit note" : "New note";
    body.innerHTML = `
      <div class="note-form">
        <label>Title<input id="noteTitle" maxlength="120" value="${esc(n?.title || "")}" placeholder="e.g. Project cleanup, photo archive, open questions"></label>
        <label>Content <span class="small" id="noteTok"></span>
          <textarea id="noteContent" placeholder="Markdown. Findings, context, folders to focus on… The model sees this as background whenever the note is attached to a chat.">${esc(n?.content || "")}</textarea></label>
        <p class="error" id="noteErr" hidden></p>
        <div class="drawer-actions">
          <button class="primary" id="noteSave">Save</button>
          <button id="noteCancel">Cancel</button>
        </div>
      </div>`;
    const ta = $("noteContent");
    const tok = () => { $("noteTok").textContent = `~${Math.ceil(ta.value.length / 4).toLocaleString()} tokens`; };
    ta.oninput = tok; tok();
    $("noteCancel").onclick = () => (n ? openNote(n.id) : closeDrawer());
    $("noteSave").onclick = async () => {
      const payload = { title: $("noteTitle").value, content: ta.value };
      try {
        const saved = n ? await api(`/api/notes/${n.id}`, { method: "PUT", body: JSON.stringify(payload) })
                        : await api("/api/notes", { method: "POST", body: JSON.stringify(payload) });
        loadNotes();
        if (currentId && currentNotes.some((x) => x.id === saved.id)) openChatQuiet();
        openNote(saved.id);
      } catch (e) {
        $("noteErr").hidden = false; $("noteErr").textContent = e.message;
      }
    };
    (n ? ta : $("noteTitle")).focus();
  }

  async function openChatQuiet() {
    // refresh chips + context meter of the open chat without re-rendering messages
    try { const c = await api(`/api/chats/${currentId}`); showNoteChips(c.notes); showContext(c.context); } catch {}
  }

  async function openNote(id) {
    const body = $("drawerBody");
    $("drawerTitle").textContent = "Note";
    body.innerHTML = '<p class="small">Loading…</p>';
    openDrawer(false);
    try {
      const n = await api(`/api/notes/${id}`);
      const attached = currentNotes.some((x) => x.id === n.id);
      const canAttach = currentId && !attached && currentNotes.length < NOTE_LIMIT;
      body.innerHTML = `<h3 style="margin-top:0">${esc(n.title)}</h3>
        <div class="drawer-actions">
          <button class="primary" id="noteNewChat">New chat with this note</button>
          ${attached ? '<button id="noteDetach">Detach from this chat</button>'
            : currentId ? `<button id="noteAttach" ${canAttach ? "" : "disabled title=\"A chat can have at most 3 notes\""}>Attach to this chat</button>` : ""}
          <button id="noteEdit">✎ Edit</button>
          <button id="noteDownload">⬇ Download</button>
          ${n.source_chat ? '<button id="noteSource">Open source chat</button>' : ""}
          <button class="danger" id="noteDelete">Delete</button>
        </div>
        <p class="small">~${Math.ceil(n.content.length / 4).toLocaleString()} tokens when attached to a chat` +
        `${n.source_chat ? "" : " · written by hand"} · ${new Date(n.created * 1000).toLocaleString()}</p>
        <div class="content">${md(n.content)}</div>`;
      $("noteNewChat").onclick = () => { closeDrawer(); startChatWithNotes([n.id]); };
      $("noteEdit").onclick = () => noteEditor(n);
      $("noteDownload").onclick = () => download(`/api/notes/${n.id}/export`);
      if ($("noteAttach")) $("noteAttach").onclick = async () => {
        try { await setChatNotes([...currentNotes.map((x) => x.id), n.id]); openNote(n.id); } catch (e) { alert(e.message); }
      };
      if ($("noteDetach")) $("noteDetach").onclick = async () => {
        try { await setChatNotes(currentNotes.filter((x) => x.id !== n.id).map((x) => x.id)); openNote(n.id); } catch (e) { alert(e.message); }
      };
      if (n.source_chat) $("noteSource").onclick = () => { closeDrawer(); location.hash = n.source_chat; };
      $("noteDelete").onclick = async () => {
        if (!confirm("Delete this note? Chats that use it will no longer see it.")) return;
        await api(`/api/notes/${n.id}`, { method: "DELETE" });
        closeDrawer(); loadNotes();
        if (currentId) openChatQuiet();
      };
    } catch (e) {
      body.innerHTML = `<p class="error">${esc(String(e.message || e))}</p>`;
    }
  }

  async function startChatWithNotes(noteIds) {
    if (generating) return;
    const c = await api("/api/chats", { method: "POST", body: JSON.stringify({ notes: noteIds }) });
    location.hash = c.id;   // hashchange -> openChat
    await openChat(c.id);
    input.focus();
  }

  async function download(url) {
    const r = await fetch(url);
    if (!r.ok) { alert("Download failed: " + r.statusText); return; }
    const name = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "export.md";
    const a = el("a"); a.href = URL.createObjectURL(await r.blob()); a.download = name;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  // Summarize the current chat into a note; returns the note.
  async function summarizeCurrent(buttons) {
    buttons.forEach((b) => { b.disabled = true; b.dataset.label = b.textContent; b.textContent = "Summarizing…"; });
    try {
      const note = await api(`/api/chats/${currentId}/summarize`, { method: "POST" });
      loadNotes();
      return note;
    } catch (e) {
      alert("Summary failed: " + e.message + (e.message.includes("nothing") ? "" : "\nIs Ollama running?"));
      return null;
    } finally {
      buttons.forEach((b) => { b.disabled = false; b.textContent = b.dataset.label; });
    }
  }

  function showNoteChips(notesList) {
    currentNotes = notesList || [];
    const box = $("noteChips");
    box.innerHTML = "";
    box.hidden = !currentNotes.length;
    for (const n of currentNotes) {
      const chip = el("span", "note-chip");
      const open = el("button", "note-open"); open.textContent = "📎 " + n.title;
      open.title = "Attached note: its content is background context for this chat. Click to view.";
      open.onclick = () => openNote(n.id);
      const x = el("button", "note-x", "✕"); x.title = "Detach from this chat";
      x.onclick = async () => {
        if (generating) return;
        try { await setChatNotes(currentNotes.filter((y) => y.id !== n.id).map((y) => y.id)); } catch (e) { alert(e.message); }
      };
      chip.append(open, x); box.append(chip);
    }
  }

  function ago(ts) {
    const s = Date.now() / 1000 - ts;
    if (s < 60) return "now";
    if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h";
    if (s < 7 * 86400) return Math.floor(s / 86400) + "d";
    return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function newChat() {
    hideSettingsPage(); hideFilesPage();
    currentId = null;
    history.replaceState(null, "", location.pathname);
    $("chatTitle").textContent = "New chat";
    messagesEl.innerHTML = "";
    messagesEl.append(emptyEl);
    showContext(null);
    showNoteChips([]);
    $("chatActions").hidden = true;
    loadChats();
    input.focus();
  }

  async function openChat(id) {
    if (generating) return;
    try {
      const chat = await api(`/api/chats/${id}`);
      currentId = id;
      $("chatTitle").textContent = chat.title;
      messagesEl.innerHTML = "";
      renderHistory(chat.messages);
      showContext(chat.context);
      showNoteChips(chat.notes);
      $("chatActions").hidden = !chat.messages.length;
      loadChats();
      scrollBottom(true);
    } catch { newChat(); }
  }

  // ---------- context meter ----------
  // Levels are measured against the usable budget (window minus the space reserved for the answer),
  // because that is where compaction starts.
  const dismissedBanner = new Set();
  function ctxLevel(c, extra = 0) {
    if (!c || !c.limit) return { pct: 0, level: "ok" };
    const budget = c.budget || c.limit;
    const pct = ((c.estimated_tokens || 0) + extra) / budget;
    const trimming = (c.compacted_results || 0) + (c.dropped_turns || 0) > 0;
    return { pct, level: pct >= 0.9 || trimming ? "full" : pct >= 0.7 ? "warn" : "ok" };
  }

  function showContext(c) {
    const chip = $("ctx");
    lastContext = c;
    updateInputTokens();
    if (!c || !c.limit) { chip.hidden = true; $("ctxBanner").hidden = true; return; }
    const used = Math.max(c.prompt_tokens || 0, c.estimated_tokens || 0);
    const { pct, level } = ctxLevel(c);
    chip.hidden = false;
    chip.className = "ctx" + (level === "ok" ? "" : " " + level);
    chip.innerHTML = `<span class="meter"><i style="width:${Math.min(100, pct * 100).toFixed(0)}%"></i></span>` +
      `Context ${(used / 1000).toFixed(1)}k / ${Math.round(c.limit / 1024)}k · ${c.remembered_docs} files`;
    chip.title = `${Math.round(pct * 100)}% of the usable context (${Math.round((c.budget || c.limit) / 1000)}k; the rest is reserved for the answer).\n` +
      `${c.compacted_results} old tool results compacted, ${c.dropped_turns} old turns trimmed.\n` +
      "Every file seen in this chat stays remembered, so follow-up questions keep working.";

    const banner = $("ctxBanner");
    if (level !== "full" || dismissedBanner.has(currentId)) { banner.hidden = true; return; }
    banner.hidden = false;
    if (c.dropped_turns) {
      $("ctxBannerTitle").textContent = "Context is full";
      $("ctxBannerMsg").textContent = `The model can no longer see the ${c.dropped_turns} oldest exchange${c.dropped_turns > 1 ? "s" : ""} word for word (the files they found are still remembered). Summarize into a note and continue fresh, or save the chat.`;
    } else {
      $("ctxBannerTitle").textContent = "Context almost full";
      $("ctxBannerMsg").textContent = c.compacted_results
        ? `Older search results are being compacted to fit (${c.compacted_results} so far). Consider continuing in a fresh chat with a summary note.`
        : `${Math.round(pct * 100)}% of the usable context is used. Soon older results will be compacted.`;
    }
  }

  // ---------- token counter under the textbox ----------
  const estTokens = (text) => Math.ceil(text.length / 4);   // ~4 chars per token for English prose
  function updateInputTokens() {
    const n = estTokens(input.value.trim());
    const out = $("inputTokens");
    out.textContent = `~${n.toLocaleString()} token${n === 1 ? "" : "s"}`;
    const { level } = ctxLevel(lastContext, n);
    out.classList.toggle("warn", level === "warn");
    out.classList.toggle("full", level === "full");
    out.title = level === "full" ? "This message plus the conversation fills the context: older content will be compacted or trimmed."
              : level === "warn" ? "The conversation is getting close to the context limit."
              : "Estimated tokens in your message";
  }

  // ---------- answer stats ----------
  function addStats(turn, st) {
    if (!st) return;
    turn.querySelector(".stats")?.remove();
    const parts = [st.model, `${(st.output_tokens || 0).toLocaleString()} tokens out`];
    if (st.tokens_per_s) parts.push(`${st.tokens_per_s} tok/s`);
    parts.push(`${st.seconds}s`);
    if (st.context_tokens) parts.push(`${(st.context_tokens / 1000).toFixed(1)}k context`);
    if (st.model_calls > 1) parts.push(`${st.model_calls} model calls`);
    turn.append(el("div", "stats", parts.filter(Boolean).map((p) => `<span>${esc(String(p))}</span>`).join("")));
  }

  // ---------- rendering ----------
  const emptyEl = $("empty"), emptyAdd = $("emptyAdd");   // detached from the DOM while a chat is open
  function scrollBottom(force) {
    const near = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 140;
    if (force || near) messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function addUser(text) {
    emptyEl.remove();
    const m = el("div", "msg user");
    const b = el("div", "bubble"); b.textContent = text;
    m.append(b); messagesEl.append(m);
    return m;
  }

  function addTurn() {
    const m = el("div", "msg assistant");
    messagesEl.append(m);
    return m;
  }

  function fmtArgs(args) {
    return Object.entries(args || {}).map(([k, v]) => k === "query" || k === "name" ? `"${v}"` : `${k}=${v}`).join(" · ");
  }

  function addStep(turn, call) {
    let steps = turn.lastElementChild;
    if (!steps || !steps.classList.contains("steps")) { steps = el("div", "steps"); turn.append(steps); }
    const d = el("details", "step pending");
    d.open = !!prefs.expandSteps;
    d.dataset.name = call.name;
    d.innerHTML = `<summary><span class="spin"></span><span class="name">${esc(call.name)}</span>` +
      `<span class="args">${esc(fmtArgs(call.arguments))}</span><span class="meta"></span></summary><div class="body"></div>`;
    steps.append(d);
    return d;
  }

  const refId = (ref) => String(ref || "").replace(/^F/i, "");
  const at = (r) => r.page ? `p.${r.page}` : r.line ? `L${r.line}` : "";

  function fillStep(step, name, result) {
    step.classList.remove("pending");
    step.querySelector(".spin")?.remove();
    const meta = step.querySelector(".meta"), body = step.querySelector(".body");
    let html;
    if (result && result.error && !result.ref) {
      meta.textContent = "error"; html = `<p class="error">${esc(result.error)}</p>`;
    } else if (name === "search_content") {
      const rows = Array.isArray(result?.results) ? result.results : [];
      meta.textContent = `${rows.length} passage${rows.length === 1 ? "" : "s"}`;
      html = rows.length ? rows.map((r) => `<div class="passage"><div class="p-head">${fileLink(refId(r.ref), r.page, r.line)} ` +
          `<b>${esc(r.name || "")}</b> <span class="small">${esc(r.folder || "")}</span></div>` +
          `<div class="p-text">${esc((r.text || "").slice(0, 500))}</div></div>`).join("")
        : `<p>${esc(result?.hint || "No matches")}</p>`;
      if (result?.note) html = `<p class="small">${esc(result.note)}</p>` + html;
    } else if (name === "find_files") {
      const rows = Array.isArray(result?.files) ? result.files : [];
      meta.textContent = `${(result?.total ?? rows.length).toLocaleString()} file${result?.total === 1 ? "" : "s"}` +
        (result?.total > rows.length ? ` · ${rows.length} shown` : "") + (result?.total_size ? ` · ${result.total_size}` : "");
      html = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Ref</th><th>Name</th><th>Folder</th><th>Size</th><th>Modified</th></tr></thead><tbody>` +
        rows.map((r) => `<tr><td>${fileLink(refId(r.ref))}</td><td>${esc(r.name || "")}</td><td class="small">${esc(r.folder || "")}</td>` +
          `<td>${esc(r.size_h || "")}</td><td>${esc(r.modified || "")}</td></tr>`).join("") + "</tbody></table></div>"
        : `<p>${esc(result?.hint || "No files")}</p>`;
    } else if (name === "find_duplicates" && Array.isArray(result?.results)) {
      meta.textContent = `${(result.sets || 0).toLocaleString()} sets · ${result.reclaimable} reclaimable`;
      html = (result.note ? `<p class="small">${esc(result.note)}</p>` : "") + (result.results.length ? result.results.map((g) =>
        `<p><b>${g.copies} copies</b> × ${esc(g.size_each)} · ${esc(g.reclaimable)} reclaimable</p><div class="table-wrap"><table><tbody>` +
        g.files.map((r) => `<tr><td>${fileLink(refId(r.ref))}</td><td>${esc(r.name || "")}</td><td class="small">${esc(r.folder || "")}</td>` +
          `<td>${esc(r.modified || "")}</td></tr>`).join("") + "</tbody></table></div>").join("") : "<p>No duplicates found.</p>");
    } else if (name === "folder_sizes" && Array.isArray(result?.children)) {
      meta.textContent = `${esc(result.size)} · ${(result.files || 0).toLocaleString()} files`;
      html = `<p class="small">${esc(result.path)}</p><div class="table-wrap"><table><thead><tr><th>Folder</th><th>Size</th><th>Files</th><th>Newest</th></tr></thead><tbody>` +
        result.children.map((c) => `<tr><td class="small">${esc(c.path)}</td><td>${esc(c.size)}</td><td>${(c.files || 0).toLocaleString()}</td>` +
          `<td>${esc(c.newest || "")}</td></tr>`).join("") + "</tbody></table></div>";
    } else if (name === "cleanup_report" && Array.isArray(result?.items)) {
      meta.textContent = `${result.items.length} checks`;
      html = `<div class="table-wrap"><table><thead><tr><th>What</th><th>Files</th><th>Size</th></tr></thead><tbody>` +
        result.items.map((i) => `<tr><td>${esc(i.label)}<br><span class="small">${esc(i.hint || "")}</span></td>` +
          `<td>${(i.files || 0).toLocaleString()}</td><td>${esc(i.size_h)}</td></tr>`).join("") + "</tbody></table></div>";
    } else if (name === "file_stats" && Array.isArray(result?.rows)) {
      const key = result.group_by;
      meta.textContent = `${result.groups} group${result.groups === 1 ? "" : "s"} by ${key}`;
      html = `<div class="table-wrap"><table><thead><tr><th>${esc(key)}</th><th>Files</th><th>Size</th><th>Newest</th></tr></thead><tbody>` +
        result.rows.map((r) => `<tr><td>${esc(String(r.label || (r[key] ?? "")))}</td><td>${(r.files || 0).toLocaleString()}</td>` +
          `<td>${esc(r.size_h || "")}</td><td>${esc(r.newest || "")}</td></tr>`).join("") + "</tbody></table></div>";
    } else if ((name === "file_info" || name === "read_file") && result?.ref) {
      const id = refId(result.ref);
      if (name === "read_file") {
        const ch = result.chunks || [];
        meta.textContent = result.error ? "not readable" : ch.length ? `chunks ${ch[0].chunk}–${ch[ch.length - 1].chunk} of ${result.total_chunks}` : "nothing to read";
        html = `<p>${fileLink(id)} <b>${esc(result.name || "")}</b> <span class="small">${esc(result.path || "")}</span></p>` +
          (result.error ? `<p class="small">${esc(result.error)}</p>` : "") +
          ch.map((c) => `<div class="passage"><div class="p-head">${esc(at(c))}${c.section ? ` · ${esc(c.section)}` : ""}</div>` +
            `<div class="p-text">${esc(c.text.slice(0, 600))}${c.text.length > 600 ? "…" : ""}</div></div>`).join("");
      } else {
        meta.textContent = [result.category_label, result.size, result.modified].filter(Boolean).join(" · ");
        html = `<p>${fileLink(id)} <b>${esc(result.name || "")}</b></p><p class="small">${esc(result.path || "")}</p>`;
      }
    } else {
      html = `<pre><code>${esc(JSON.stringify(result, null, 2))}</code></pre>`;
    }
    body.innerHTML = html;
  }

  function renderHistory(msgs) {
    if (!msgs.length) { messagesEl.append(emptyEl); return; }
    emptyEl.remove();
    let turn = null;
    const pending = [];
    for (const m of msgs) {
      if (m.role === "user") { addUser(m.content); turn = null; continue; }
      if (!turn) turn = addTurn();
      if (m.role === "assistant") {
        if (m.content && m.content.trim()) turn.append(el("div", "content", md(m.content)));
        for (const tc of m.tool_calls || []) pending.push(addStep(turn, tc.function));
        if (m.stats) addStats(turn, m.stats);
      } else if (m.role === "tool") {
        const step = pending.shift();
        if (step) { let r; try { r = JSON.parse(m.content); } catch { r = m.content; } fillStep(step, m.tool_name, r); }
      }
    }
  }

  // ---------- sending ----------
  async function readSSE(r, handle) {
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        if (chunk.startsWith("data: ")) handle(JSON.parse(chunk.slice(6)));
      }
    }
  }

  async function send(text) {
    text = text.trim();
    if (!text || generating) return;
    if (!currentId) {
      const c = await api("/api/chats", { method: "POST", body: "{}" });
      currentId = c.id;
      history.replaceState(null, "", "#" + c.id);
    }
    input.value = ""; autosize(); updateInputTokens();
    addUser(text);
    const turn = addTurn();
    setGenerating(true);
    scrollBottom(true);

    let contentEl = null, raw = "", thinkEl = null, frame = 0;
    const pending = [];
    const paint = () => { frame = 0; if (contentEl) { contentEl.innerHTML = md(raw); scrollBottom(); } };

    const handle = ({ type, data }) => {
      switch (type) {
        case "turn_start":
          raw = ""; contentEl = el("div", "content cursor"); turn.append(contentEl); thinkEl = null; break;
        case "thinking":
          if (!thinkEl) { thinkEl = el("div", "thinking"); turn.insertBefore(thinkEl, contentEl); }
          thinkEl.textContent += data; break;
        case "token":
          raw += data; if (!frame) frame = requestAnimationFrame(paint); break;
        case "turn_end":
          if (contentEl) { contentEl.classList.remove("cursor"); if (!raw.trim()) contentEl.remove(); else contentEl.innerHTML = md(raw); }
          contentEl = null; break;
        case "tool_call":
          pending.push(addStep(turn, data)); scrollBottom(); break;
        case "tool_result": {
          const step = pending.shift(); if (step) fillStep(step, data.name, data.result); break;
        }
        case "context": showContext(data); break;
        case "stats": addStats(turn, data); scrollBottom(); break;
        case "progress": {
          const step = pending[0]; if (step) step.querySelector(".meta").textContent = data; break;
        }
        case "warning": turn.append(el("p", "small", esc(data))); break;
        case "error": turn.append(el("p", "error", esc(data))); break;
      }
    };

    try {
      const r = await fetch(`/api/chats/${currentId}/messages`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: text }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      await readSSE(r, handle);
    } catch (e) {
      turn.append(el("p", "error", esc(String(e.message || e))));
    } finally {
      $("chatActions").hidden = false;
      if (contentEl) contentEl.classList.remove("cursor");
      turn.querySelectorAll(".step.pending .spin").forEach((s) => s.remove());
      setGenerating(false);
      loadChats().then(() => { const c = chats.find((c) => c.id === currentId); if (c) $("chatTitle").textContent = c.title; });
    }
  }

  function setGenerating(on) {
    generating = on;
    $("send").hidden = on; $("stop").hidden = !on;
  }

  // ---------- file drawer ----------
  async function openFile(id, page, line) {
    const body = $("drawerBody");
    $("drawerTitle").textContent = "F" + id;
    body.innerHTML = '<p class="small">Loading…</p>';
    openDrawer(true);
    try {
      const d = await api(`/api/files/${id}`);
      const raw = `/api/files/${d.id}/raw`;
      const meta = Object.entries(d.meta || {}).filter(([k]) => !["page_count", "slide_count", "lines"].includes(k))
        .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(Array.isArray(v) ? v.join(", ") : String(v))}</dd>`).join("");
      const contentText = { ready: `indexed · ${d.chunks} chunks`, pending: "waiting to be read", error: "could not be read",
        skipped: "not read (too large, or content indexing is off)", none: "not readable (name and metadata only)" }[d.content] || d.content;
      const ext = (d.ext || "").toLowerCase();
      let media = "";
      if (IMG_EXT.has(ext)) media = `<div class="preview-media"><img src="${raw}" alt="${esc(d.name)}" loading="lazy"></div>`;
      else if (VIDEO_EXT.has(ext)) media = `<div class="preview-media"><video src="${raw}" controls preload="metadata"></video></div>`;
      else if (AUDIO_EXT.has(ext)) media = `<div class="preview-media"><audio src="${raw}" controls preload="metadata"></audio></div>`;
      body.innerHTML = `
        <div class="file-head">${fileIcon(d, true)}<div><h3>${esc(d.name)}</h3><div class="small">${esc(d.folder)}</div></div></div>
        <div class="drawer-actions">
          <button class="primary" id="fileAsk">Ask about this file</button>
          <a class="btn" href="${raw}" target="_blank" rel="noopener">Open ↗</a>
          <button id="fileReveal" title="Show in Explorer / Finder (on this computer)">Show in folder</button>
          <button id="fileCopy">Copy path</button>
          <a class="btn" href="${raw}?download=1">⬇</a>
        </div>
        ${d.warning ? `<p class="error">⚠ ${esc(d.warning)}</p>` : ""}${d.error ? `<p class="error">${esc(d.error)}</p>` : ""}
        ${media}
        <dl class="kv">
          <dt>Path</dt><dd>${esc(d.path)}</dd>
          <dt>Type</dt><dd>${esc(d.category_label)} · .${esc(d.ext || "(none)")}${d.kind ? ` · ${esc(d.kind)}` : ""}</dd>
          <dt>Size</dt><dd>${esc(d.size_h)} (${(d.size || 0).toLocaleString()} bytes)</dd>
          <dt>Modified</dt><dd>${esc(d.modified || "—")}</dd>
          <dt>Created</dt><dd>${esc(d.created || "—")}</dd>
          <dt>Content</dt><dd>${esc(contentText)}${d.lines ? ` · ${d.lines.toLocaleString()} lines` : ""}${d.pages > 1 ? ` · ${d.pages} pages` : ""}</dd>
          ${d.title && d.title !== d.name ? `<dt>Title</dt><dd>${esc(d.title)}</dd>` : ""}
          <dt>Same folder</dt><dd><a href="#" id="fileFolder">${d.same_folder.toLocaleString()} file${d.same_folder === 1 ? "" : "s"}</a></dd>
          ${meta}
        </dl>
        ${d.copies?.length ? `<div class="section-title">Identical copies (${d.copies.length})</div><ul class="copies">${d.copies.map((c) =>
          `<li>${fileLink(c.id)} ${esc(c.folder)}\\${esc(c.name)} <span class="small">${esc(c.modified || "")}</span></li>`).join("")}</ul>` : ""}
        <div id="filePassage"></div>
        ${d.outline?.length ? `<div class="section-title">Outline</div><ol class="refs outline">${d.outline.map((o) =>
          `<li><a href="#" data-page="${o.page ?? ""}" data-chunk="${o.chunk}">${esc(o.section)}</a>${d.pages > 1 ? ` <span class="small">p.${o.page}</span>` : ""}</li>`).join("")}</ol>` : ""}`;
      $("fileAsk").onclick = () => { closeDrawer(); if (onFiles() || onSettings()) closeSettings(); input.value = `About F${d.id} (${d.name}): `; autosize(); input.focus(); };
      $("fileCopy").onclick = () => copy(d.path, $("fileCopy"));
      $("fileReveal").onclick = async () => { try { await api(`/api/files/${d.id}/reveal`, { method: "POST" }); } catch (e) { alert(e.message); } };
      $("fileFolder").onclick = (e) => { e.preventDefault(); closeDrawer(); browseFolder(d.folder); };
      body.querySelectorAll(".outline a").forEach((a) => a.onclick = (e) => {
        e.preventDefault(); showPassage(d, { page: a.dataset.page && d.pages > 1 ? Number(a.dataset.page) : null, chunk: Number(a.dataset.chunk) });
      });
      if (d.content === "ready") showPassage(d, { page: page && d.pages > 1 ? page : null, line });
    } catch (e) {
      body.innerHTML = `<p class="error">${esc(String(e.message || e))}</p>`;
    }
  }

  const LINED = new Set(["text", "code", "config", "markdown"]);
  async function showPassage(d, { page, line, chunk }) {
    const box = $("filePassage");
    if (!box) return;
    try {
      if (LINED.has(d.kind) && chunk == null) {
        const start = Math.max(1, (line || 1) - 15);
        const r = await api(`/api/files/${d.id}/lines?start=${start}&count=${line ? 200 : 300}`);
        box.innerHTML = `<div class="section-title">Text · lines ${r.start}–${r.start + r.lines.length - 1} of ${r.total.toLocaleString()}</div>` +
          `<pre class="code-view">${r.lines.map((t, i) => `<div class="ln${r.start + i === line ? " hit" : ""}"><span>${r.start + i}</span><code>${esc(t) || " "}</code></div>`).join("")}</pre>` +
          (r.more ? `<button class="link-btn" id="moreText">Continue reading</button>` : "");
        if ($("moreText")) $("moreText").onclick = () => showPassage(d, { line: r.start + r.lines.length + 15 });
        if (line) box.querySelector(".ln.hit")?.scrollIntoView({ block: "center" });
        return;
      }
      const q = page ? `page=${page}&count=4` : `chunk=${chunk || 0}&count=4`;
      const r = await api(`/api/files/${d.id}/read?${q}`);
      const where = page ? `Page ${page}` : r.chunks.length ? `From chunk ${r.chunks[0].chunk}` : "";
      box.innerHTML = `<details class="passage-box" open><summary class="section-title">Text · ${esc(where)}</summary>` +
        r.chunks.map((c) => `<div class="passage"><div class="p-head">${c.page && d.pages > 1 ? `p.${c.page}` : ""}${c.section ? ` · ${esc(c.section)}` : ""}</div>` +
          `<div class="p-text">${md(c.text)}</div></div>`).join("") +
        (r.next_chunk != null ? `<button class="link-btn" id="moreText">Continue reading</button>` : "") + "</details>";
      if (page || chunk) box.scrollIntoView({ block: "start", behavior: "smooth" });
      if ($("moreText")) $("moreText").onclick = () => showPassage(d, { chunk: r.next_chunk });
    } catch (e) {
      box.innerHTML = `<p class="error">${esc(e.message)}</p>`;
    }
  }
  const closeDrawer = () => { $("drawer").classList.remove("open"); $("drawer").setAttribute("aria-hidden", "true"); };

  // ---------- status ----------
  async function refreshStatus() {
    try {
      const s = await api("/api/status");
      $("ollamaDot").className = "dot " + (s.ollama ? "ok" : "bad");
      $("modelName").textContent = s.ollama ? s.chat_model : "Ollama not reachable";
      $("appVersion").textContent = s.version;
      $("modelChip").textContent = s.chat_model;
      // placement: how much of each loaded model sits in VRAM (100% = no CPU offload); not utilization
      const placement = s.loaded.map((m) => {
        const pct = m.size ? Math.round(100 * m.vram / m.size) : 0;
        return `${m.model.split(":")[0]} ${pct >= 100 ? "in VRAM" : `${pct}% VRAM / ${100 - pct}% CPU`}`;
      }).join(" · ") || "no model loaded (loads on first message)";
      const g = s.gpu;
      $("gpuInfo").textContent = (g ? `GPU load ${Math.round(g.util)}% · ${(g.mem_used_mb / 1024).toFixed(1)}/${(g.mem_total_mb / 1024).toFixed(0)} GB\n` : "") + placement;
      $("gpuInfo").title = "GPU load = live utilization from nvidia-smi.\n'in VRAM' = the whole model is loaded on the GPU (fastest); " +
        "a CPU share means it did not fit and runs slower.\nModels stay loaded 30 min after the last request.";
      const C = s.catalog;
      const pct = C.chunks ? Math.min(100, 100 * s.vectors_loaded / C.chunks) : (C.files ? 100 : 0);
      $("indexBar").style.width = pct.toFixed(1) + "%";
      $("indexText").textContent = `${C.files.toLocaleString()} files · ${C.readable.toLocaleString()} read`;
      $("indexText").title = `${C.roots} mapped folder(s), ${C.files.toLocaleString()} files, content of ${C.readable.toLocaleString()} indexed ` +
        `(${C.chunks.toLocaleString()} chunks, ${s.vectors_indexed.toLocaleString()} embedded, ${s.vectors_loaded.toLocaleString()} loaded for semantic search)` +
        (C.pending ? `\n${C.pending.toLocaleString()} files waiting to be read` : "");
      $("libCount").textContent = C.files ? C.files.toLocaleString() : "";
      showJob(s.worker);
      emptyAdd.hidden = C.files > 0;
      $("reloadIndex").hidden = s.vectors_indexed <= s.vectors_loaded || !!s.worker.jobs;
    } catch {
      $("ollamaDot").className = "dot bad"; $("modelName").textContent = "server offline";
    }
  }

  const PHASE = { scan: "Scanning", quick: "Quick-checking same-size files", hash: "Comparing same-size files", media: "Reading image details",
    content: "Reading contents", embed: "Embedding" };
  let lastJobs = 0;
  function showJob(w) {
    const p = w.progress || {};
    let text = "";
    if (w.current === "remove") text = "Removing folder…";
    else if (p.phase === "scan") text = `Scanning ${p.root || ""}: ${(p.seen || 0).toLocaleString()} files`;
    else if (p.phase) text = `${PHASE[p.phase]} ${(p.done || 0).toLocaleString()} / ${(p.total || 0).toLocaleString()}` + (p.current ? ` · ${p.current}` : "");
    else if (w.jobs) text = "Starting…";
    $("jobText").hidden = !w.jobs;
    $("jobText").textContent = w.jobs ? `⏳ ${text}${w.jobs > 1 ? ` (+${w.jobs - 1} queued)` : ""}` : "";
    $("jobBox").hidden = !w.jobs && !w.error;
    $("jobStatus").textContent = w.jobs ? text : w.error ? "⚠ " + w.error : "";
    $("jobBox").classList.toggle("error", !w.jobs && !!w.error);
    $("jobStop").hidden = !w.jobs;
    $("jobCount").textContent = w.jobs > 1 ? `${w.jobs - 1} more queued` : "";
    $("jobBar").style.width = p.total ? (100 * (p.done || 0) / p.total).toFixed(1) + "%" : p.phase === "scan" ? "100%" : "0%";
    if (onFiles() && (w.jobs || lastJobs)) {
      loadOverview();
      if (!w.jobs) { loadFiles(true); loadFolders(FO.path); loadCleanup(); }
    }
    lastJobs = w.jobs;
  }

  // ---------- wiring ----------
  function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 200) + "px"; }
  input.addEventListener("input", () => { autosize(); updateInputTokens(); });
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing) return;
    const sendIt = prefs.enterSends ? !e.shiftKey && !e.ctrlKey : (e.ctrlKey || e.metaKey);
    if (sendIt) { e.preventDefault(); send(input.value); }
  });
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  $("stop").onclick = () => currentId && api(`/api/chats/${currentId}/stop`, { method: "POST" });
  $("newChat").onclick = () => { if (!generating) newChat(); };
  const narrow = () => matchMedia("(max-width: 900px)").matches;
  $("toggleSidebar").onclick = () => $("sidebar").classList.toggle(narrow() ? "shown" : "hidden");
  const closeSidebar = () => $("sidebar").classList[narrow() ? "remove" : "add"](narrow() ? "shown" : "hidden");
  $("closeSidebar").onclick = closeSidebar;
  $("sidebarBackdrop").onclick = closeSidebar;   // tap outside the overlay menu on narrow screens
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && narrow() && $("sidebar").classList.contains("shown")) closeSidebar();
  });
  $("sidebar").addEventListener("click", (e) => {   // on phones, close the overlay after picking a chat
    if (narrow() && e.target.closest(".chat-item a, .new-chat")) $("sidebar").classList.remove("shown");
  });
  $("closeDrawer").onclick = closeDrawer;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a.doc");
    if (a) { e.preventDefault(); openFile(a.dataset.id, a.dataset.page ? Number(a.dataset.page) : null, a.dataset.line ? Number(a.dataset.line) : null); }
  });
  emptyEl.querySelectorAll(".suggestions button").forEach((b) => b.onclick = () => send(b.textContent));
  $("reloadIndex").onclick = async () => {
    $("reloadIndex").textContent = "Loading…";
    await api("/api/reload-index", { method: "POST" }).catch(() => {});
    $("reloadIndex").textContent = "Load newly indexed vectors";
    refreshStatus();
  };
  // ---------- save / summarize / reset ----------
  $("exportChat").onclick = () => currentId && download(`/api/chats/${currentId}/export`);
  $("bannerSave").onclick = () => currentId && download(`/api/chats/${currentId}/export`);
  $("summarizeChat").onclick = async () => {
    if (!currentId || generating) return;
    const note = await summarizeCurrent([$("summarizeChat")]);
    if (note) openNote(note.id);
  };
  $("bannerContinue").onclick = async () => {
    if (!currentId || generating) return;
    const note = await summarizeCurrent([$("bannerContinue"), $("summarizeChat")]);
    if (note) startChatWithNotes([note.id]);
  };
  $("bannerReset").onclick = () => {
    if (generating) return;
    if (confirm("Start a new empty chat? This chat stays saved in the sidebar.")) newChat();
  };
  $("bannerDismiss").onclick = () => { dismissedBanner.add(currentId); $("ctxBanner").hidden = true; };

  // ---------- settings page ----------
  const dlg = $("settingsPage");
  const onSettings = () => !dlg.hidden;
  function showSettingsPage() {
    hideFilesPage(); closeDrawer();
    $("chatView").hidden = true; dlg.hidden = false;
    $("openSettings").classList.add("active");
    document.title = "Settings · Files Explorer";
    syncPrefControls();
    api("/api/settings").then(fillServerSettings).catch((e) => { $("savedNote").textContent = "⚠ " + e.message; });
    loadModels();
    loadKeys();
  }
  function hideSettingsPage() {
    if (dlg.hidden) return;
    dlg.hidden = true; $("chatView").hidden = false;
    $("openSettings").classList.remove("active");
    document.title = "Files Explorer";
  }
  let savedTimer = 0;
  function syncPrefControls() {
    dlg.querySelectorAll(".seg[data-pref]").forEach((seg) =>
      seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === prefs[seg.dataset.pref])));
    dlg.querySelectorAll("input[data-pref]").forEach((i) => { i.checked = !!prefs[i.dataset.pref]; });
  }
  function fillServerSettings({ values, options, model_info }) {
    const mi = model_info;
    $("modelInfo").textContent = mi
      ? [mi.params, mi.quant, `${mi.size_gb} GB`, mi.context_length && `max context ${Math.round(mi.context_length / 1024)}k`,
         mi.capabilities.filter((c) => c !== "completion").join(", "),
         mi.loaded ? (mi.vram_pct >= 100 ? "loaded in VRAM" : `loaded ${mi.vram_pct}% VRAM`) : "not loaded"].filter(Boolean).join(" · ")
      : "";
    $("setThink").disabled = !!mi && !mi.capabilities.includes("thinking");
    $("setThink").title = $("setThink").disabled ? "This model has no reasoning mode" : "";
    $("modelChip").textContent = values.chat_model;
    $("setModel").innerHTML = options.chat_model.map((m) => `<option ${m === values.chat_model ? "selected" : ""}>${esc(m)}</option>`).join("");
    $("setCtx").innerHTML = options.num_ctx.map((n) => `<option value="${n}" ${n === values.num_ctx ? "selected" : ""}>${Math.round(n / 1024)}k tokens</option>`).join("");
    $("setTemp").value = values.temperature; $("tempVal").textContent = Number(values.temperature).toFixed(2);
    $("setThink").checked = values.think;
    $("setRounds").value = values.max_tool_rounds;
    $("setApi").checked = !!values.api_enabled;
    $("setContent").checked = !!values.content_index;
    $("setEmbed").checked = !!values.embed_content;
    $("setSniff").checked = !!values.sniff_unknown;
    $("setHidden").checked = !!values.skip_hidden;
    $("setDupes").checked = !!values.find_duplicates;
    $("setMedia").checked = !!values.read_media;
    $("setArchives").checked = !!values.list_archives;
    $("setMaxMb").value = values.max_content_mb;
    if (document.activeElement !== $("setExclude")) $("setExclude").value = values.exclude_dirs;
    $("setLength").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === values.answer_length));
  }
  async function saveSetting(name, value, noteId = "savedNote") {
    try {
      fillServerSettings(await api("/api/settings", { method: "PUT", body: JSON.stringify({ [name]: value }) }));
      $(noteId).textContent = "Saved ✓";
      refreshStatus();
      if (name === "num_ctx" && currentId) api(`/api/chats/${currentId}`).then((c) => showContext(c.context)).catch(() => {});
    } catch (e) {
      $(noteId).textContent = "⚠ " + e.message;
    }
    clearTimeout(savedTimer); savedTimer = setTimeout(() => { $(noteId).textContent = ""; }, 2500);
  }
  function openSettings() {
    if (location.hash === "#settings") showSettingsPage();
    else location.hash = "settings";   // hashchange -> showSettingsPage; browser Back returns to the chat
  }
  $("openSettings").onclick = openSettings;
  $("modelChip").onclick = () => { openSettings(); setTimeout(() => $("setModel").scrollIntoView({ block: "center" }), 80); };

  // ---------- model management ----------
  async function loadModels() {
    let d;
    try { d = await api("/api/models"); } catch (e) { $("modelList").innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
    $("modelSuggestions").innerHTML = d.suggested.map((n) => `<option value="${esc(n)}">`).join("");
    $("modelList").innerHTML = "";
    for (const m of d.installed) {
      const row = el("div", "model-row" + (m.name === d.current ? " current" : ""));
      const badges = m.embedding ? '<span class="badge">embeddings</span>'
        : m.tools ? '<span class="badge ok">tools</span>' : '<span class="badge no">no tools</span>';
      const inUse = m.name === d.current || m.name === d.embed_model || m.name === d.embed_model + ":latest";
      row.innerHTML = `<div class="grow"><span class="mname">${esc(m.name)}</span>${badges}` +
        `${m.name === d.current ? '<span class="badge cur">in use</span>' : ""}` +
        `<div class="mmeta">${[m.params, m.quant, `${m.size_gb} GB`, m.context_length && `ctx ${Math.round(m.context_length / 1024)}k`].filter(Boolean).join(" · ")}</div></div>`;
      if (m.tools && m.name !== d.current) {
        const use = el("button"); use.textContent = "Use";
        use.onclick = () => saveSetting("chat_model", m.name).then(loadModels);
        row.append(use);
      }
      if (!inUse) {
        const del = el("button", "danger"); del.textContent = "Delete"; del.title = "Remove from disk";
        del.onclick = async () => {
          if (!confirm(`Delete ${m.name} from disk (${m.size_gb} GB)? You can download it again later.`)) return;
          try { await api(`/api/models/${encodeURIComponent(m.name)}`, { method: "DELETE" }); } catch (e) { alert(e.message); }
          loadModels();
        };
        row.append(del);
      }
      $("modelList").append(row);
    }
  }

  let pullAbort = null;
  const fmtGB = (b) => (b / 2 ** 30).toFixed(b >= 2 ** 30 ? 1 : 2) + " GB";
  async function pullModel(name) {
    name = name.trim();
    if (!name || pullAbort) return;
    const box = $("pullBox");
    box.hidden = false; box.classList.remove("error");
    $("pullStatus").textContent = `Checking ${name}…`; $("pullBytes").textContent = ""; $("pullBar").style.width = "0%";
    $("pullBtn").disabled = true; $("pullCancel").hidden = false;
    pullAbort = new AbortController();
    let installed = null, failed = null;
    try {
      const r = await fetch("/api/models/pull", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }), signal: pullAbort.signal });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      await readSSE(r, ({ type, data }) => {
        if (type === "progress") {
          $("pullStatus").textContent = data.status;
          if (data.total) {
            $("pullBar").style.width = (100 * data.completed / data.total).toFixed(1) + "%";
            $("pullBytes").textContent = `${fmtGB(data.completed)} / ${fmtGB(data.total)}`;
          }
        } else if (type === "installed") installed = data;
        else if (type === "error") failed = data;
        else if (type === "cancelled") failed = "Download cancelled";
      });
    } catch (e) {
      failed = e.name === "AbortError" ? "Download cancelled (partial data is kept; downloading again resumes)" : e.message;
    } finally {
      pullAbort = null; $("pullBtn").disabled = false; $("pullCancel").hidden = true;
    }
    if (failed) { box.classList.add("error"); $("pullStatus").textContent = failed; $("pullBytes").textContent = ""; return; }
    if (installed && !installed.tools) {
      box.classList.add("error");
      $("pullStatus").textContent = `${installed.name} installed, but it has no tool calling, so the agent cannot use it.`;
      loadModels(); return;
    }
    $("pullStatus").textContent = `${installed.name} ready, switching…`; $("pullBar").style.width = "100%";
    await saveSetting("chat_model", installed.name);
    $("pullStatus").textContent = `Now using ${installed.name}. It loads onto the GPU with your next message.`;
    $("pullName").value = "";
    loadModels();
  }
  $("pullBtn").onclick = () => pullModel($("pullName").value);
  $("pullName").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); pullModel($("pullName").value); } });
  $("pullCancel").onclick = () => pullAbort?.abort();
  const closeSettings = () => { location.hash = currentId || ""; if (!currentId) { history.replaceState(null, "", location.pathname); hideSettingsPage(); hideFilesPage(); } };
  $("closeSettings").onclick = closeSettings;
  $("toggleSidebar2").onclick = () => $("toggleSidebar").click();
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && (onSettings() || onFiles()) && !$("drawer").classList.contains("open")) closeSettings();
  });
  dlg.querySelectorAll(".seg[data-pref] button").forEach((b) => b.onclick = () => {
    prefs[b.parentElement.dataset.pref] = b.dataset.v; savePrefs(); applyPrefs(); syncPrefControls();
  });
  dlg.querySelectorAll("input[data-pref]").forEach((i) => i.onchange = () => {
    prefs[i.dataset.pref] = i.checked; savePrefs(); applyPrefs();
  });
  $("setModel").onchange = (e) => saveSetting("chat_model", e.target.value).then(loadModels);
  $("setCtx").onchange = (e) => saveSetting("num_ctx", Number(e.target.value));
  $("setThink").onchange = (e) => saveSetting("think", e.target.checked);
  $("setRounds").onchange = (e) => saveSetting("max_tool_rounds", Number(e.target.value));
  $("setTemp").oninput = (e) => { $("tempVal").textContent = Number(e.target.value).toFixed(2); };
  $("setTemp").onchange = (e) => saveSetting("temperature", Number(e.target.value));
  $("setContent").onchange = (e) => saveSetting("content_index", e.target.checked, "mapNote");
  $("setEmbed").onchange = (e) => saveSetting("embed_content", e.target.checked, "mapNote");
  $("setSniff").onchange = (e) => saveSetting("sniff_unknown", e.target.checked, "mapNote");
  $("setHidden").onchange = (e) => saveSetting("skip_hidden", e.target.checked, "mapNote");
  $("setDupes").onchange = (e) => saveSetting("find_duplicates", e.target.checked, "mapNote");
  $("setMedia").onchange = (e) => saveSetting("read_media", e.target.checked, "mapNote");
  $("setArchives").onchange = (e) => saveSetting("list_archives", e.target.checked, "mapNote");
  $("setMaxMb").onchange = (e) => saveSetting("max_content_mb", Number(e.target.value), "mapNote");
  $("setExclude").onchange = (e) => saveSetting("exclude_dirs", e.target.value.split(/[,\n]/).map((s) => s.trim()).filter(Boolean).join(","), "mapNote");

  // ---------- API access ----------
  const apiBase = () => `${location.origin}/api/v1`;
  let exKey = "YOUR_API_KEY", exLang = "curl";
  function renderExample() {
    const b = apiBase(), k = exKey;
    const ex = {
      curl: `# find files by name / type / size / date (fast, no LLM)
curl -H "Authorization: Bearer ${k}" "${b}/files?ext=pdf&after=2024&sort=size&limit=20"

# search inside files
curl -H "Authorization: Bearer ${k}" "${b}/search?q=database+password&category=config&limit=5"

# ask the agent (save:true keeps the conversation; reuse chat_id for follow-ups)
curl -X POST ${b}/ask -H "Authorization: Bearer ${k}" -H "Content-Type: application/json" \\
  -d '{"question": "Which folders use the most space?", "save": true}'`,
      python: `import requests

API = "${b}"
H = {"Authorization": "Bearer ${k}"}

big = requests.get(f"{API}/files", headers=H, params={"min_size": "1GB", "sort": "size"}).json()
for f in big["files"]:
    print(f["ref"], f["size_h"], f["folder"], f["name"])

hits = requests.get(f"{API}/search", headers=H, params={"q": "invoice total", "ext": "pdf"}).json()
for h in hits["results"]:
    print(h["ref"], h.get("page"), h["name"], h["text"][:80])

r = requests.post(f"{API}/ask", headers=H, timeout=600, json={
    "question": "Where are my tax documents from 2024?", "save": True}).json()
print(r["answer"])

# follow-up in the same conversation
r = requests.post(f"{API}/ask", headers=H, timeout=600, json={
    "question": "Which of those are larger than 5 MB?", "chat_id": r["chat_id"]}).json()
print(r["answer"])`,
      ps: `$H = @{ Authorization = "Bearer ${k}" }
$API = "${b}"

(Invoke-RestMethod "$API/files?category=video&sort=size&limit=10" -Headers $H).files | Format-Table ref, size_h, name, folder

(Invoke-RestMethod "$API/stats?by=category" -Headers $H).rows | Format-Table label, files, size_h

$body = @{ question = "Find duplicate-looking backups in my Documents folder"; save = $true } | ConvertTo-Json
(Invoke-RestMethod "$API/ask" -Method Post -Headers $H -ContentType "application/json" -Body $body -TimeoutSec 600).answer`,
    };
    $("exCode").textContent = ex[exLang];
  }

  async function loadKeys() {
    $("apiBase").textContent = apiBase();
    let d;
    try { d = await api("/api/keys"); } catch (e) { $("keyList").innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
    $("apiReach").innerHTML = d.local_only
      ? `Reachable only from this computer (server listens on ${esc(d.bind_host)}). For other machines, start it with <code>python -m filerag web --host 0.0.0.0</code>.`
      : `<span class="reach-warn">Listening on ${esc(d.bind_host)}:${d.port} — other machines on your network can reach it. API calls need a key, but the web UI itself has no login.</span>`;
    $("keyList").innerHTML = d.keys.length ? "" : '<p class="note">No keys yet.</p>';
    for (const k of d.keys) {
      const row = el("div", "model-row");
      row.innerHTML = `<div class="grow"><span class="mname">${esc(k.name)}</span><span class="badge">${esc(k.prefix)}…</span>` +
        `<div class="mmeta">created ${new Date(k.created * 1000).toLocaleString()} · ` +
        `${k.last_used ? `last used ${ago(k.last_used) === "now" ? "just now" : ago(k.last_used) + " ago"} · ${k.uses} call${k.uses === 1 ? "" : "s"}` : "never used"}</div></div>`;
      const rev = el("button", "danger"); rev.textContent = "Revoke";
      rev.onclick = async () => {
        if (!confirm(`Revoke key "${k.name}"? Programs using it will stop working.`)) return;
        await api(`/api/keys/${k.id}`, { method: "DELETE" }).catch((e) => alert(e.message));
        loadKeys();
      };
      row.append(rev); $("keyList").append(row);
    }
    renderExample();
  }

  $("setLength").querySelectorAll("button").forEach((b) => b.onclick = () => saveSetting("answer_length", b.dataset.v));
  $("setApi").onchange = async (e) => {
    await saveSetting("api_enabled", e.target.checked);
    $("apiNote").textContent = e.target.checked ? "API on" : "API off";
    setTimeout(() => { $("apiNote").textContent = ""; }, 2500);
  };
  $("keyCreate").onclick = async () => {
    const name = $("keyName").value.trim();
    if (!name) { $("keyName").focus(); return; }
    try {
      const k = await api("/api/keys", { method: "POST", body: JSON.stringify({ name }) });
      $("keyValue").textContent = k.key; $("keyReveal").hidden = false; $("keyName").value = "";
      exKey = k.key; loadKeys();
    } catch (e) { alert(e.message); }
  };
  $("keyName").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("keyCreate").click(); } });
  const copy = async (text, btn) => {
    try { await navigator.clipboard.writeText(text); } catch {
      const t = el("textarea"); t.value = text; document.body.append(t); t.select(); document.execCommand("copy"); t.remove();
    }
    const old = btn.textContent; btn.textContent = "Copied ✓"; setTimeout(() => { btn.textContent = old; }, 1500);
  };
  $("keyCopy").onclick = () => copy($("keyValue").textContent, $("keyCopy"));
  $("exCopy").onclick = () => copy($("exCode").textContent, $("exCopy"));
  $("exTabs").querySelectorAll("button").forEach((b) => b.onclick = () => {
    exLang = b.dataset.ex;
    $("exTabs").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    renderExample();
  });

  window.addEventListener("hashchange", () => {
    const id = location.hash.slice(1);
    if (id === "settings") { showSettingsPage(); return; }
    if (id === "files") { showFilesPage(); return; }
    hideSettingsPage(); hideFilesPage();
    if (id && id !== currentId) openChat(id);
  });

  // ---------- files page ----------
  const filesPage = $("filesPage");
  const onFiles = () => !filesPage.hidden;
  const F = { mode: "name", offset: 0, total: 0, folder: "", extra: null };   // extra = {label, filters} from clean-up
  function showFilesPage() {
    hideSettingsPage(); closeDrawer();
    $("chatView").hidden = true; filesPage.hidden = false;
    $("openFiles").classList.add("active");
    document.title = "Files · Files Explorer";
    loadOverview().then(() => loadFiles(true));
    loadFolders(FO.path);
    loadCleanup();
  }
  function hideFilesPage() {
    if (filesPage.hidden) return;
    filesPage.hidden = true; $("chatView").hidden = false;
    $("openFiles").classList.remove("active");
    document.title = "Files Explorer";
  }

  const ROOT_STATUS = { ready: "ok", error: "no" };
  let overviewBusy = false;
  async function loadOverview() {
    if (overviewBusy) return;
    overviewBusy = true;
    let d;
    try { d = await api("/api/overview"); } catch (e) { $("rootList").innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
    finally { overviewBusy = false; }
    catLabels = d.category_labels;
    // folders
    $("rootList").innerHTML = d.roots_list.length ? "" : '<p class="note">No folders mapped yet. Add one above, e.g. your Documents or Projects folder.</p>';
    for (const r of d.roots_list) {
      const row = el("div", "model-row root-row");
      const st = r.status || "ready";
      row.innerHTML = `<div class="grow"><span class="mname">${esc(r.path)}</span>` +
        (st !== "ready" ? `<span class="badge ${ROOT_STATUS[st] ?? ""}">${st === "error" ? "" : "⏳ "}${esc(st)}</span>` : "") +
        `<div class="mmeta">${r.files.toLocaleString()} files · ${esc(r.size_h)} · content of ${r.readable.toLocaleString()} indexed` +
        (r.pending ? ` · ${r.pending.toLocaleString()} waiting` : "") + (r.last_scan ? ` · scanned ${ago(r.last_scan)}${ago(r.last_scan) === "now" ? "" : " ago"}` : "") +
        (r.error ? ` · <span class="reach-warn">${esc(r.error)}</span>` : "") + "</div></div>";
      const browse = el("button"); browse.textContent = "Browse";
      browse.onclick = () => { $("froot").value = String(r.id); loadFiles(true); $("browseCard").scrollIntoView({ behavior: "smooth" }); };
      const re = el("button"); re.textContent = "Rescan"; re.title = "Look for new, changed and deleted files";
      re.onclick = async () => { await api(`/api/roots/${r.id}/scan`, { method: "POST" }).catch((e) => alert(e.message)); refreshStatus(); };
      const del = el("button", "danger"); del.textContent = "Remove";
      del.title = "Forget this folder (nothing is deleted on disk)";
      del.onclick = async () => {
        if (!confirm(`Stop mapping ${r.path}?\nIts ${r.files.toLocaleString()} files are removed from the map. Nothing is deleted on disk.`)) return;
        await api(`/api/roots/${r.id}`, { method: "DELETE" }).catch((e) => alert(e.message));
        refreshStatus();
      };
      row.append(browse, re, del);
      $("rootList").append(row);
    }
    // categories
    const max = Math.max(1, ...d.categories.map((c) => c.files));
    $("typeTotal").textContent = d.files ? `${d.files.toLocaleString()} files · ${d.size_h}` : "";
    $("catList").innerHTML = d.categories.length ? "" : '<p class="note">Nothing mapped yet.</p>';
    const curCat = $("fcat").value;
    for (const c of d.categories) {
      const b = el("button", "cat-row" + (c.category === curCat ? " on" : ""));
      b.dataset.cat = c.category;
      b.innerHTML = `<span class="cname"><span class="swatch" style="background:var(--cat-${c.category})"></span>${esc(c.label)}</span>` +
        `<span class="cbar"><i style="width:${(100 * c.files / max).toFixed(1)}%;background:var(--cat-${c.category})"></i></span>` +
        `<span class="cnum">${c.files.toLocaleString()} · ${fmtSize(c.size)}</span>`;
      b.onclick = () => { $("fcat").value = $("fcat").value === c.category ? "" : c.category; loadFiles(true); };
      $("catList").append(b);
    }
    // extensions
    $("extTotal").textContent = d.extensions.length ? `top ${d.extensions.length}` : "";
    $("extCloud").innerHTML = "";
    const curExt = $("fext").value.trim().toLowerCase();
    for (const x of d.extensions) {
      const ext = x.ext === "(no extension)" ? "none" : x.ext;
      const b = el("button", "ext-chip" + (curExt === ext ? " on" : ""));
      b.dataset.ext = ext;
      b.innerHTML = `.${esc(x.ext === "(no extension)" ? "—" : x.ext)} <small>${x.files.toLocaleString()} · ${esc(x.size_h)}</small>`;
      b.title = `${x.files.toLocaleString()} files, ${x.size_h}`;
      b.onclick = () => { $("fext").value = $("fext").value.trim().toLowerCase() === ext ? "" : ext; loadFiles(true); };
      $("extCloud").append(b);
    }
    // filter selects
    const cat = $("fcat").value, root = $("froot").value;
    $("fcat").innerHTML = '<option value="">All types</option>' + d.categories.map((c) =>
      `<option value="${c.category}" ${c.category === cat ? "selected" : ""}>${esc(c.label)} (${c.files.toLocaleString()})</option>`).join("");
    $("froot").innerHTML = '<option value="">All folders</option>' + d.roots_list.map((r) =>
      `<option value="${r.id}" ${String(r.id) === root ? "selected" : ""}>${esc(r.path)}</option>`).join("");
  }

  function fileQuery() {
    const size = $("fsize").value;
    const [minS, maxS] = size ? size.split("-") : ["", ""];
    return {
      category: $("fcat").value, ext: $("fext").value.trim(), root: $("froot").value, folder: F.folder,
      after: $("fdate").value, min_size: minS, max_size: maxS, ...(F.extra?.filters || {}),
    };
  }

  function renderActiveFilters() {
    const box = $("activeFilters");
    box.innerHTML = "";
    if (F.folder) {
      const b = el("button", "filter-chip"); b.textContent = "Folder: " + F.folder;
      b.onclick = () => { F.folder = ""; loadFiles(true); };
      box.append(b);
    }
    if (F.extra) {
      const b = el("button", "filter-chip"); b.textContent = F.extra.label;
      b.onclick = () => { F.extra = null; loadFiles(true); };
      box.append(b);
    }
  }

  let filesSeq = 0;
  async function loadFiles(reset) {
    if (reset) F.offset = 0;
    const seq = ++filesSeq;
    const q = $("fq").value.trim();
    const base = fileQuery();
    renderActiveFilters();
    document.querySelectorAll(".cat-row").forEach((b) => b.classList.toggle("on", b.dataset.cat === base.category));
    document.querySelectorAll(".ext-chip").forEach((b) => b.classList.toggle("on", b.dataset.ext === (base.ext || "").toLowerCase()));
    const list = $("fileList");
    if (reset) list.innerHTML = '<p class="note">Searching…</p>';
    try {
      if (F.mode === "content") {
        if (!q) { list.innerHTML = '<p class="note">Type words to search inside your files (documents, code, notes, e-mails…).</p>'; $("fileSummary").textContent = ""; $("moreFiles").hidden = true; return; }
        const d = await api(`/api/search?${qs({ q, ...base, limit: 60 })}`);
        if (seq !== filesSeq) return;
        list.innerHTML = d.results.length ? "" : '<p class="note">No content matches. Try other words, or Name search.</p>';
        $("fileSummary").textContent = `${d.results.length} file${d.results.length === 1 ? "" : "s"} with matching content`;
        for (const h of d.results) list.append(fileRow({ ...h, id: h.file_id }, h, q));
        $("moreFiles").hidden = true;
      } else if (F.mode === "dupes") {
        const d = await api(`/api/duplicates?${qs({ name: q, ...base, limit: 25, offset: F.offset })}`);
        if (seq !== filesSeq) return;
        if (reset) list.innerHTML = d.results.length ? "" : `<p class="note">${d.unchecked ? "Still comparing files…" : "No identical files match."}</p>`;
        F.offset += d.results.length;
        $("fileSummary").textContent = `${d.groups.toLocaleString()} set${d.groups === 1 ? "" : "s"} · ${d.files.toLocaleString()} files · ${d.wasted_h} reclaimable` +
          (d.unchecked ? ` · comparing ${d.unchecked.toLocaleString()} more…` : "");
        for (const g of d.results) {
          const box = el("div", "dup-group");
          box.innerHTML = `<div class="dup-head"><b>${g.copies} identical copies</b><span>${esc(g.size_h)} each</span>` +
            `<span class="saved">${esc(g.wasted_h)} reclaimable</span></div>`;
          for (const f of g.files) box.append(fileRow(f));
          if (g.copies > g.files.length) box.append(el("p", "note", `+ ${g.copies - g.files.length} more copies`));
          list.append(box);
        }
        $("moreFiles").hidden = F.offset >= d.groups;
        $("moreFiles").textContent = `Load more (${(d.groups - F.offset).toLocaleString()} sets left)`;
      } else {
        const d = await api(`/api/files?${qs({ name: q, ...base, sort: $("fsort").value, limit: 100, offset: F.offset })}`);
        if (seq !== filesSeq) return;
        if (reset) list.innerHTML = d.files.length ? "" : `<p class="note">${q || Object.values(base).some(Boolean) ? "No files match." : "No files mapped yet: add a folder above."}</p>`;
        F.total = d.total;
        F.offset += d.files.length;
        $("fileSummary").textContent = `${d.total.toLocaleString()} file${d.total === 1 ? "" : "s"} · ${d.total_size_h}`;
        for (const f of d.files) list.append(fileRow(f));
        $("moreFiles").hidden = F.offset >= d.total;
        $("moreFiles").textContent = `Load more (${(d.total - F.offset).toLocaleString()} left)`;
      }
    } catch (e) {
      if (seq === filesSeq) list.innerHTML = `<p class="error">${esc(e.message)}</p>`;
    }
  }
  function fileRow(f, hit, q) {
    const row = el("div", "model-row file-row");
    row.dataset.id = f.id;
    row.innerHTML = fileIcon(f) +
      `<div class="grow"><span class="mname">${q ? highlight(f.name, q) : esc(f.name)}</span>` +
      (f.content === "error" ? '<span class="badge no" title="content could not be read">⚠</span>' : "") +
      (hit && hit.matches > 1 ? `<span class="badge">${hit.matches} matches</span>` : "") +
      `<div class="fpath">${esc(f.folder || "")}</div>` +
      (hit ? `<div class="snip">${at(hit) ? `<b>${esc(at(hit))}</b> ` : ""}${highlight(focusText(hit.text || "", q).slice(0, 400), q)}</div>` : "") + "</div>" +
      (hit ? "" : `<div class="fside">${esc(f.size_h || "")}<br>${f.width ? `${f.width}×${f.height}` : esc((f.taken || f.modified || "").slice(0, 10))}</div>`);
    row.onclick = () => openFile(f.id, hit?.page, hit?.line);
    return row;
  }

  function browseFolder(folder) {
    if (!onFiles()) location.hash = "files";
    F.folder = folder; $("froot").value = ""; $("fq").value = "";
    setMode("name");
    setTimeout(() => { loadFiles(true); $("browseCard").scrollIntoView({ behavior: "smooth" }); }, onFiles() ? 0 : 120);
  }

  function setMode(mode) {
    F.mode = mode;
    $("fmode").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x.dataset.v === mode));
    $("fq").placeholder = mode === "content" ? "Words inside files, e.g. connection string, quarterly revenue"
      : mode === "dupes" ? "Optional: words in the file name" : "File name, e.g. invoice 2024 or *.tar.gz";
    $("fsort").disabled = mode !== "name";
  }

  // ---------- folder sizes ----------
  const FO = { path: null };
  async function loadFolders(path) {
    let d;
    try { d = await api(`/api/folders?${qs({ path: path || "" })}`); } catch (e) { $("folderList").innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
    FO.path = d.path;
    $("folderPath").textContent = d.path || "Mapped folders";
    $("folderUp").hidden = !d.path;
    $("folderFiles").hidden = !d.path;
    $("folderTotal").textContent = d.files ? `${d.size_h} · ${d.files.toLocaleString()} files` : "";
    const max = Math.max(1, ...d.children.map((c) => c.size));
    $("folderList").innerHTML = d.children.length ? "" : `<p class="note">${d.path ? "No sub-folders." : "No folders mapped yet."}</p>`;
    if (d.files_here) {
      const b = el("div", "cat-row folder-row");
      b.innerHTML = `<span class="cname small">(files directly here)</span><span></span><span class="cnum">${d.files_here.toLocaleString()} · ${esc(d.size_here_h)}</span>`;
      $("folderList").append(b);
    }
    for (const c of d.children) {
      const b = el("button", "cat-row folder-row");
      b.title = `${c.path}\n${c.files.toLocaleString()} files · newest ${c.newest || "—"}`;
      b.innerHTML = `<span class="cname">📁 ${esc(c.name)}</span>` +
        `<span class="cbar"><i style="width:${(100 * c.size / max).toFixed(1)}%;background:var(--accent)"></i></span>` +
        `<span class="cnum">${esc(c.size_h)} · ${c.files.toLocaleString()}</span>`;
      b.onclick = () => loadFolders(c.path);
      $("folderList").append(b);
    }
    FO.parent = d.parent;
  }
  $("folderUp").onclick = () => loadFolders(FO.parent);
  $("folderFiles").onclick = () => { if (FO.path) { F.extra = null; browseFolder(FO.path); } };

  // ---------- clean-up ----------
  async function loadCleanup() {
    let d;
    try { d = await api("/api/cleanup"); } catch (e) { $("cleanList").innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
    const total = d.items.reduce((n, i) => n + i.size, 0);
    $("cleanTotal").textContent = total ? `up to ${fmtSize(total)}` : "";
    $("cleanList").innerHTML = "";
    for (const i of d.items) {
      const b = el("button", "cat-row clean-row");
      b.innerHTML = `<span class="cname">${esc(i.label)}<small>${esc(i.hint)}</small></span>` +
        `<span class="cnum"><b>${esc(i.size_h)}</b><br>${i.files.toLocaleString()} file${i.files === 1 ? "" : "s"}</span>`;
      b.disabled = !i.files;
      b.onclick = () => {
        F.folder = ""; $("fq").value = "";
        if (i.key === "duplicates") { F.extra = null; setMode("dupes"); }
        else { F.extra = { label: i.label, filters: i.filters }; setMode("name"); $("fsort").value = "size"; }
        loadFiles(true);
        $("browseCard").scrollIntoView({ behavior: "smooth" });
      };
      $("cleanList").append(b);
    }
  }

  let fqTimer = 0;
  $("fq").oninput = () => { clearTimeout(fqTimer); fqTimer = setTimeout(() => loadFiles(true), F.mode === "content" ? 450 : 200); };
  $("fq").addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(fqTimer); loadFiles(true); } });
  $("fmode").querySelectorAll("button").forEach((b) => b.onclick = () => { setMode(b.dataset.v); loadFiles(true); });
  ["fcat", "froot", "fdate", "fsize", "fsort"].forEach((id) => $(id).onchange = () => loadFiles(true));
  let fextTimer = 0;
  $("fext").oninput = () => { clearTimeout(fextTimer); fextTimer = setTimeout(() => loadFiles(true), 300); };
  $("moreFiles").onclick = () => loadFiles(false);
  $("addPathBtn").onclick = async () => {
    const path = $("addPath").value.trim();
    if (!path) { $("addPath").focus(); return; }
    try {
      const r = await api("/api/roots", { method: "POST", body: JSON.stringify({ path }) });
      $("addNote").textContent = r.created ? "Folder added, scanning ✓" : "Already mapped, rescanning ✓";
      $("addPath").value = "";
      refreshStatus(); loadOverview();
    } catch (e) { $("addNote").textContent = "⚠ " + e.message; }
    setTimeout(() => { $("addNote").textContent = ""; }, 4000);
  };
  $("addPath").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("addPathBtn").click(); } });
  $("rescanAll").onclick = async () => { await api("/api/roots/scan-all", { method: "POST" }).catch((e) => alert(e.message)); refreshStatus(); };
  $("jobStop").onclick = async () => { await api("/api/jobs/stop", { method: "POST" }).catch(() => {}); refreshStatus(); };
  const openFiles = () => { if (location.hash === "#files") showFilesPage(); else location.hash = "files"; };
  $("openFiles").onclick = openFiles;
  emptyAdd.onclick = openFiles;
  $("closeFiles").onclick = closeSettings;
  $("toggleSidebar3").onclick = () => $("toggleSidebar").click();

  applyPrefs();
  updateInputTokens();
  const initial = location.hash.slice(1);
  if (initial === "settings") { loadChats(); showSettingsPage(); }
  else if (initial === "files") { loadChats(); showFilesPage(); }
  else if (initial) openChat(initial); else loadChats();
  loadNotes();
  $("newNote").onclick = (e) => {
    e.preventDefault();
    openDrawer(false);
    noteEditor(null);
  };
  // poll fast while scanning / indexing, slowly otherwise
  (async function poll() { await refreshStatus(); setTimeout(poll, lastJobs || onFiles() ? 2500 : 10000); })();
})();
