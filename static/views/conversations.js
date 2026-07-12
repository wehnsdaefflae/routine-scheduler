// Conversations tab: a sidebar of all conversations (searchable, tag-filtered, deletable),
// a chat-first center pane (components/chat.js — replies prominent, tool work folded), and
// the artifact panel (components/artifacts.js). A conversation is one continuous run:
// sending into a live reply injects; sending into a finished one resumes it in place, so
// the view remounts its tail after every send.

import { api, apiUpload } from "/static/api.js";
import { navigate } from "/static/router.js";
import { liveTail } from "/static/stream.js";
import { createChat } from "/static/components/chat.js";
import { createArtifacts } from "/static/components/artifacts.js";
import { busy, chip, el, emptyState, relTime, tagChip, toast } from "/static/util.js";

const TERMINAL = new Set(["finished", "failed", "aborted"]);
const WORKING = new Set(["running", "starting", "queued"]);
const PREFILL_KEY = "conv-new-prefill";

export async function render(view, slug, _query = {}) {
  view.classList.add("conv-view");
  const sideList = el("div", { class: "conv-list" });
  const sideTags = el("div", { class: "conv-tags" });
  const search = el("input", { type: "search", placeholder: "search conversations…" });
  const newBtn = el("a", { class: "btn primary small", href: "#/conversations" }, "+ new");
  const side = el("aside", { class: "conv-side" },
    el("div", { class: "row", style: "gap:6px" }, search, newBtn), sideTags, sideList);
  const main = el("section", { class: "conv-main" });
  const art = el("aside", { class: "conv-art", hidden: !slug });
  view.append(el("div", { class: "conv-layout" }, side, main, art));

  let items = [], activeTag = "";
  let cleanup = [];   // per-mount teardowns (tail, timers, artifact blobs)

  // ---- sidebar --------------------------------------------------------------------------------
  async function loadList() {
    try { items = await api("/api/conversations"); } catch { return; }
    renderList();
  }

  function renderList() {
    const q = search.value.trim().toLowerCase();
    const tags = [...new Set(items.flatMap((i) => i.tags || []))].sort();
    sideTags.replaceChildren(...tags.map((t) => tagChip(t, {
      active: t === activeTag,
      onClick: () => { activeTag = activeTag === t ? "" : t; renderList(); } })));
    const shown = items.filter((i) =>
      (!activeTag || (i.tags || []).includes(activeTag))
      && (!q || `${i.title} ${i.snippet} ${(i.tags || []).join(" ")}`.toLowerCase().includes(q)));
    sideList.replaceChildren();
    if (!shown.length) {
      sideList.append(emptyState("💬", items.length ? "No matches" : "No conversations yet",
        items.length ? "" : "Start one — the first message is the task."));
      return;
    }
    for (const it of shown) {
      sideList.append(el("a", {
        class: `conv-item${it.slug === slug ? " on" : ""}`,
        href: `#/conversations/${it.slug}` },
        el("div", { class: "conv-item-head" },
          el("span", { class: `dot ${it.state}` }),
          el("span", { class: "conv-title" }, it.title || it.slug),
          el("span", { class: "faint small", style: "margin-left:auto" }, relTime(it.updated))),
        it.snippet ? el("div", { class: "conv-snippet" }, it.snippet) : null,
        it.question ? el("div", { class: "small", style: "color:var(--warn)" }, "❓ waiting for you") : null));
    }
  }
  search.oninput = renderList;

  // ---- the center pane ------------------------------------------------------------------------
  const unmount = () => { for (const fn of cleanup.splice(0)) { try { fn(); } catch { /* gone */ } } };

  if (!slug) mountComposerOnly();
  else await mountConversation();

  loadList();
  const listTimer = setInterval(loadList, 20000);
  const onBus = (e) => {
    const ev = e.detail || {};
    if (ev.event === "run_finished" || ev.event === "run_started") loadList();
    if (ev.event === "run_finished" && ev.routine === slug && document.hidden
        && "Notification" in window && Notification.permission === "granted") {
      new Notification("conversation reply ready", { body: (ev.summary || "").slice(0, 120) });
    }
  };
  window.addEventListener("rsched-bus", onBus);
  return () => { unmount(); clearInterval(listTimer); window.removeEventListener("rsched-bus", onBus); };

  // ---- new-conversation composer ---------------------------------------------------------------
  function mountComposerOnly() {
    const text = el("textarea", { rows: 5,
      placeholder: "What should the agent do? The first message becomes the conversation's task…" });
    const prefill = sessionStorage.getItem(PREFILL_KEY);
    if (prefill) { text.value = prefill; sessionStorage.removeItem(PREFILL_KEY); }
    const workdir = el("input", { type: "text", placeholder: "~/path/to/project (optional)" });
    const shellChk = el("input", { type: "checkbox" });
    const epSel = el("select", {}, el("option", { value: "" }, "system model (default)"));
    const modelIn = el("input", { type: "text", placeholder: "model id", hidden: true });
    api("/api/settings/endpoints").then((d) => {
      for (const e of d.endpoints || []) epSel.append(el("option", {}, e.name));
    }).catch(() => {});
    epSel.onchange = () => { modelIn.hidden = !epSel.value; };
    const { picker, files, clearFiles } = filePicker();
    const send = el("button", { class: "btn primary" }, "start conversation");
    send.onclick = async () => {
      if (!text.value.trim()) { toast("write the first message"); return; }
      if (epSel.value && !modelIn.value.trim()) { toast("enter a model id (or pick the system model)"); return; }
      send.disabled = true;
      try {
        const fd = new FormData();
        fd.append("text", text.value);
        if (workdir.value.trim()) fd.append("workdir", workdir.value.trim());
        if (shellChk.checked) fd.append("shell", "1");
        if (epSel.value) { fd.append("endpoint", epSel.value); fd.append("model", modelIn.value.trim()); }
        for (const f of files()) fd.append("files", f);
        const r = await apiUpload("/api/conversations", fd);
        clearFiles();
        navigate(`#/conversations/${r.slug}`);
      } catch (err) { toast(err.message, 5000, { error: true }); send.disabled = false; }
    };
    main.replaceChildren(
      el("div", { class: "page-head" }, el("div", {},
        el("div", { class: "kicker" }, "conversations"),
        el("h1", {}, "New conversation"))),
      el("div", { class: "panel conv-new" },
        text,
        el("div", { class: "row mt", style: "gap:8px;flex-wrap:wrap" }, picker, send),
        el("details", { class: "mt small" },
          el("summary", { style: "cursor:pointer;color:var(--muted)" }, "⚙ options: project dir, model, shell"),
          el("div", { class: "conv-opts" },
            el("label", {}, "project directory — the agent may read & edit it", workdir),
            el("label", {}, "model", el("div", { class: "row", style: "gap:6px" }, epSel, modelIn)),
            el("label", { class: "row", style: "gap:8px" }, shellChk,
              el("span", {}, "allow shell commands (the escape hatch — off by default)"))))));
    text.focus();
  }

  // ---- an existing conversation -----------------------------------------------------------------
  async function mountConversation() {
    unmount();
    let detail;
    try { detail = await api(`/api/conversations/${slug}`); }
    catch (err) {
      main.replaceChildren(emptyState("✕", "Conversation not found", err.message));
      return;
    }
    const stateChip = chip(detail.state, detail.state);
    const head = el("div", { class: "conv-head" });
    renderHead(head, detail, stateChip);
    const chatBox = el("div", { class: "conv-chat" });
    const waiting = el("div", {});
    const questionBox = el("div", {});
    const composer = buildComposer();
    main.replaceChildren(head, chatBox, waiting, questionBox, composer.node);

    const artifacts = createArtifacts((art.replaceChildren(), art), { slug });
    cleanup.push(() => artifacts.destroy());

    const chat = createChat(chatBox, {
      answer: (qid, text) => api(`/api/questions/${qid}/answer`, { method: "POST", body: { text } }),
      loadSub: (n, o) => api(`/api/runs/${detail.run_id}/transcript?sub=${n}&offset=${o}`),
      isLive: () => !TERMINAL.has(curState),
      onArtifact: () => artifacts.refresh(),
      onFork: (_title, lastUserText) => {
        sessionStorage.setItem(PREFILL_KEY, lastUserText || "");
        navigate("#/conversations");
      },
    });

    // The first message became instruction.md (composed into the system prompt), so no
    // transcript event carries it — seed the chat with it as the opening user bubble.
    if (detail.instruction?.trim()) {
      chat.add({ type: "user_injection", payload: { text: detail.instruction.trim() } });
    }

    let curState = detail.state;
    let autoscroll = true;
    const scrollDown = () => { if (autoscroll) window.scrollTo(0, document.body.scrollHeight); };
    const setState = (s) => {
      curState = s;
      stateChip.textContent = s;
      stateChip.className = `chip ${s}`;
      waiting.replaceChildren();
      if (WORKING.has(s)) waiting.append(busy(s === "queued" ? "queued for a slot…" : "working…"));
      composer.setLive(!TERMINAL.has(s));
      if (TERMINAL.has(s)) { chat.finishOpenFold(); artifacts.refresh(); }
    };
    setState(detail.state);

    if (!detail.run_id) return;   // created but never fired (shouldn't happen)
    const tail = liveTail({
      page: (o) => `/api/runs/${detail.run_id}/transcript?offset=${o}`,
      events: (o) => `/api/runs/${detail.run_id}/events?offset=${o}`,
      offset: 0,
      onEvent: (ev) => { chat.add(ev); scrollDown(); },
      onState: (s) => { setState(s.state); showQuestion(questionBox, s.question); },
      onGone: () => setState("finished"),
    });
    cleanup.push(() => tail.stop());

    // scrolling up pauses follow; back to the bottom resumes (same rule as the run view)
    let lastY = window.scrollY;
    const onScroll = () => {
      const y = window.scrollY;
      const up = y < lastY - 1;
      lastY = y;
      const atBottom = window.innerHeight + y >= document.body.scrollHeight - 80;
      if (up && !atBottom) autoscroll = false;
      else if (atBottom) autoscroll = true;
    };
    window.addEventListener("scroll", onScroll);
    cleanup.push(() => window.removeEventListener("scroll", onScroll));

    function buildComposer() {
      const input = el("textarea", { rows: 2, placeholder: "message…" });
      const { picker, files, clearFiles } = filePicker();
      const send = el("button", { class: "btn primary" }, "send");
      const node = el("div", { class: "conv-composer" }, input,
        el("div", { class: "row", style: "gap:8px" }, picker, send));
      const submit = async () => {
        if (!input.value.trim()) return;
        send.disabled = true;
        try {
          const fd = new FormData();
          fd.append("text", input.value);
          for (const f of files()) fd.append("files", f);
          const r = await apiUpload(`/api/conversations/${slug}/message`, fd);
          input.value = "";
          clearFiles();
          toast(r.delivery === "mid-run" ? "delivered — picked up next turn" : "waking the conversation…");
          if (r.delivery !== "mid-run") setTimeout(mountConversation, 700);   // reattach to the live run
        } catch (err) { toast(err.message, 5000, { error: true }); }
        send.disabled = false;
      };
      send.onclick = submit;
      input.onkeydown = (e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
      };
      return { node, setLive: () => {} };
    }
  }

  function showQuestion(box, q) {
    box.replaceChildren();
    if (!q) return;
    const input = el("input", { type: "text", placeholder: "your answer…", style: "flex:1" });
    const send = el("button", { class: "btn primary" }, "answer");
    const submit = async () => {
      if (!input.value.trim()) return;
      try {
        await api(`/api/questions/${q.qid}/answer`, { method: "POST", body: { text: input.value } });
        toast("answer sent");
        box.replaceChildren();
      } catch (err) { toast(err.message, 4000, { error: true }); }
    };
    send.onclick = submit;
    input.onkeydown = (e) => { if (e.key === "Enter") submit(); };
    box.append(el("div", { class: "panel warn mt" },
      el("div", { class: "prose" }, "❓ ", q.question || ""),
      q.default ? el("div", { class: "faint small mt" }, `↪ without an answer: ${q.default}`) : null,
      q.options?.length ? el("div", { class: "row mt" },
        q.options.map((o) => el("button", { class: "btn small", onclick: () => { input.value = o; } }, o))) : null,
      el("div", { class: "row mt" }, input, send)));
  }

  function renderHead(head, detail, stateChip) {
    const title = el("h1", { class: "conv-h1", contenteditable: "plaintext-only",
      spellcheck: "false" }, detail.title || slug);
    title.onblur = async () => {
      const t = title.textContent.trim();
      if (!t || t === detail.title) return;
      try { await api(`/api/conversations/${slug}`, { method: "PATCH", body: { title: t } }); loadList(); }
      catch (err) { toast(err.message, 4000, { error: true }); }
    };
    const tagsRow = el("span", { class: "conv-tagline" });
    const drawTags = (tags) => {
      tagsRow.replaceChildren(
        ...tags.map((t) => tagChip(t, { onRemove: async () => {
          const next = tags.filter((x) => x !== t);
          await api(`/api/conversations/${slug}`, { method: "PATCH", body: { tags: next } })
            .then(() => { drawTags(next); loadList(); }).catch((e) => toast(e.message, 3000, { error: true }));
        } })),
        el("button", { class: "btn small ghost", title: "add tag", onclick: async () => {
          const t = prompt("new tag");
          if (!t?.trim()) return;
          const next = [...tags, t.trim().toLowerCase()];
          await api(`/api/conversations/${slug}`, { method: "PATCH", body: { tags: next } })
            .then(() => { drawTags(next); loadList(); }).catch((e) => toast(e.message, 3000, { error: true }));
        } }, "+"));
    };
    drawTags(detail.tags || []);
    const del = el("button", { class: "btn small danger" }, "delete");
    del.onclick = async () => {
      if (!confirm(`Delete this conversation? It is unversioned — this cannot be undone.`)) return;
      try { await api(`/api/conversations/${slug}`, { method: "DELETE" }); navigate("#/conversations"); }
      catch (err) { toast(err.message, 4000, { error: true }); }
    };
    // capabilities: permission toggles (routine-only ones greyed), traits read-only
    const caps = el("details", { class: "small conv-caps" },
      el("summary", { style: "cursor:pointer;color:var(--muted)" },
        `⚙ capabilities · ${detail.workdir ? `project: ${detail.workdir} · ` : ""}≈${detail.budgets?.max_turns ?? 10} turns/reply`));
    const capBody = el("div", { class: "conv-opts" });
    const held = new Set((detail.permissions || []).filter((p) => p.active).map((p) => p.slug));
    for (const p of detail.permissions || []) {
      const cb = el("input", { type: "checkbox", checked: p.active || null,
        disabled: p.routine_only || null });
      cb.onchange = async () => {
        p.active ? held.delete(p.slug) : held.add(p.slug);
        p.active = !p.active;
        try { await api(`/api/conversations/${slug}/permissions`, { method: "PUT", body: { active: [...held] } }); }
        catch (err) { toast(err.message, 4000, { error: true }); cb.checked = !cb.checked; }
      };
      capBody.append(el("label", { class: `row${p.routine_only ? " faint" : ""}`,
        style: "gap:8px", title: p.routine_only ? "only meaningful for scheduled routines" : p.summary },
        cb, el("span", {}, p.slug, p.routine_only ? " (routines only)" : "")));
    }
    if (detail.traits?.length) {
      capBody.append(el("div", { class: "faint small mt" },
        "traits (its own practice files): ", detail.traits.join(", ")));
    }
    caps.append(capBody);
    head.replaceChildren(
      el("div", { class: "conv-head-row" }, stateChip, title, tagsRow,
        el("span", { style: "margin-left:auto" }), del),
      caps);
  }

  function filePicker() {
    const input = el("input", { type: "file", multiple: true, hidden: true });
    const chips = el("span", { class: "attach-chips" });
    const btn = el("button", { class: "btn small", onclick: () => input.click() }, "📎 attach");
    input.onchange = () => {
      chips.replaceChildren(...[...input.files].map((f) =>
        el("span", { class: "attach-chip" }, f.name)));
    };
    return {
      picker: el("span", { class: "row", style: "gap:6px" }, btn, input, chips),
      files: () => [...input.files],
      clearFiles: () => { input.value = ""; chips.replaceChildren(); },
    };
  }
}
