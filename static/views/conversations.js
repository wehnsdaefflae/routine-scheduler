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
import { busy, chip, el, emptyState, relTime, storage, tagChip, toast } from "/static/util.js";

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

  // Resizable panes: two drag handles; widths persist in localStorage and apply as an
  // inline grid template (only when the layout is wide enough for three panes — the
  // responsive collapse below 1100px keeps winning because the inline style is cleared).
  const widths = { side: 250, art: 320, ...JSON.parse(storage.get("conv-pane-widths") || "{}") };
  const layout = el("div", { class: "conv-layout" });
  const applyWidths = () => {
    if (window.innerWidth <= 1100) { layout.style.gridTemplateColumns = ""; return; }
    layout.style.gridTemplateColumns = slug
      ? `${widths.side}px 5px minmax(0,1fr) 5px ${widths.art}px`
      : `${widths.side}px 5px minmax(0,1fr) 0 0`;
  };
  const makeHandle = (which, grow) => {
    const h = el("div", { class: "pane-handle", title: "drag to resize" });
    h.onpointerdown = (e) => {
      e.preventDefault();
      h.setPointerCapture(e.pointerId);
      const startX = e.clientX, start = widths[which];
      h.classList.add("dragging");
      h.onpointermove = (ev) => {
        const d = (ev.clientX - startX) * grow;
        widths[which] = Math.max(which === "side" ? 170 : 220,
                                 Math.min(which === "side" ? 520 : 900, start + d));
        applyWidths();
      };
      h.onpointerup = () => {
        h.onpointermove = h.onpointerup = null;
        h.classList.remove("dragging");
        storage.set("conv-pane-widths", JSON.stringify(widths));
      };
    };
    return h;
  };
  const handleL = makeHandle("side", 1);
  const handleR = makeHandle("art", -1);
  handleR.hidden = !slug;
  layout.append(side, handleL, main, handleR, art);
  applyWidths();
  window.addEventListener("resize", applyWidths);
  view.append(layout);

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
        it.tags?.length ? el("div", { class: "conv-item-tags" },
          it.tags.map((t) => el("span", { class: "tag" }, t))) : null,
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
  return () => { unmount(); clearInterval(listTimer); window.removeEventListener("rsched-bus", onBus);
                 window.removeEventListener("resize", applyWidths); };

  // ---- new-conversation composer ---------------------------------------------------------------
  function mountComposerOnly() {
    const text = el("textarea", { rows: 5,
      placeholder: "What should the agent do? The first message becomes the conversation's task…" });
    const prefill = sessionStorage.getItem(PREFILL_KEY);
    if (prefill) { text.value = prefill; sessionStorage.removeItem(PREFILL_KEY); }
    const workdir = el("input", { type: "text", placeholder: "~/path/to/project (optional)" });
    const shellChk = el("input", { type: "checkbox" });
    const { picker, files, clearFiles, wirePaste } = filePicker();
    wirePaste(text);
    const send = el("button", { class: "btn primary" }, "start conversation");
    send.onclick = async () => {
      if (!text.value.trim()) { toast("write the first message"); return; }
      send.disabled = true;
      try {
        const fd = new FormData();
        fd.append("text", text.value);
        if (workdir.value.trim()) fd.append("workdir", workdir.value.trim());
        if (shellChk.checked) fd.append("shell", "1");
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
          el("summary", { style: "cursor:pointer;color:var(--muted)" }, "⚙ options: project dir, shell"),
          el("div", { class: "conv-opts" },
            el("label", {}, "project directory — the agent may read & edit it", workdir),
            el("label", { class: "row", style: "gap:8px" }, shellChk,
              el("span", {}, "allow shell commands (the escape hatch — off by default)")))),
        el("div", { class: "faint small mt" },
          "starts on the system default model — switch it any time at the top of the conversation")));
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
    renderHead(head, detail, stateChip, () => !TERMINAL.has(curState));
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
      const { picker, files, clearFiles, wirePaste } = filePicker();
      wirePaste(input);
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

  // The model line at the top of a conversation: shows the EFFECTIVE model (override or
  // system default) and switches it at any point — routine.yaml is patched (each reply
  // boots on it), and a live reply additionally gets the mid-run control.json switch.
  function modelControl(detail, isLive) {
    const cur = detail.models?.main;
    const sysLabel = detail.system_model
      ? `${detail.system_model.endpoint}/${detail.system_model.model}` : "system model";
    const sel = el("select", { style: "width:auto;font-size:11.5px;padding:3px 6px" },
      el("option", { value: "" }, `default · ${sysLabel}`),
      (detail.endpoints || []).map((e) =>
        el("option", { value: e, selected: cur?.endpoint === e || null }, e)));
    const modelIn = el("input", { type: "text", placeholder: "model id", value: cur?.model || "",
      hidden: !cur, style: "width:170px;font-size:11.5px;padding:3px 6px" });
    const apply = el("button", { class: "btn small primary", hidden: true }, "apply");
    sel.onchange = () => { modelIn.hidden = !sel.value; apply.hidden = false; if (sel.value) modelIn.focus(); };
    modelIn.oninput = () => { apply.hidden = false; };
    apply.onclick = async () => {
      if (sel.value && !modelIn.value.trim()) { toast("enter a model id"); return; }
      const ref = { endpoint: sel.value, model: modelIn.value.trim() };
      const models = sel.value
        ? { main: ref, subroutine: { ...ref }, tool_call: { ...ref } } : {};
      try {
        await api(`/api/conversations/${slug}`, { method: "PATCH", body: { models } });
        if (sel.value && isLive() && detail.run_id) {
          // the current reply switches too, at its next turn boundary
          await api(`/api/runs/${detail.run_id}/model`,
            { method: "POST", body: ref }).catch(() => {});
        }
        toast(sel.value ? `model → ${ref.endpoint}/${ref.model}` : `model → ${sysLabel}`);
        apply.hidden = true;
      } catch (err) { toast(err.message, 4000, { error: true }); }
    };
    return el("span", { class: "conv-model" },
      el("span", { class: "faint small" }, "model"), sel, modelIn, apply);
  }

  function renderHead(head, detail, stateChip, isLive) {
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
    // capabilities: budgets (per-reply ceilings) + permission toggles (routine-only ones
    // greyed) + traits read-only
    const caps = el("details", { class: "small conv-caps" },
      el("summary", { style: "cursor:pointer;color:var(--muted)" },
        `⚙ capabilities & budgets${detail.workdir ? ` · project: ${detail.workdir}` : ""}`));
    const capBody = el("div", { class: "conv-opts" });
    const b = detail.budgets || {};
    const numIn = (v) => el("input", { type: "number", min: "1", value: v,
      style: "width:90px;font-size:11.5px;padding:3px 6px" });
    const turnsIn = numIn(b.max_turns ?? 10);
    const minsIn = numIn(b.max_wall_clock_min ?? 30);
    const tokIn = numIn(b.max_total_tokens ?? 400000);
    const saveBudgets = el("button", { class: "btn small" }, "save budgets");
    saveBudgets.onclick = async () => {
      try {
        await api(`/api/conversations/${slug}`, { method: "PATCH", body: { budgets: {
          max_turns: +turnsIn.value || 10, max_wall_clock_min: +minsIn.value || 30,
          max_total_tokens: +tokIn.value || 400000 } } });
        toast("budgets saved — they cap EACH reply, from the next one");
      } catch (err) { toast(err.message, 4000, { error: true }); }
    };
    const budgetField = (label, input) => el("label", { style: "flex-direction:column" },
      el("span", { class: "faint" }, label), input);
    capBody.append(el("div", { class: "row", style: "gap:12px;flex-wrap:wrap;align-items:flex-end" },
      budgetField("turns / reply", turnsIn), budgetField("minutes / reply", minsIn),
      budgetField("tokens / reply", tokIn), saveBudgets));
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
      el("div", { class: "conv-head-row" }, stateChip, title,
        el("span", { style: "margin-left:auto" }), del),
      el("div", { class: "conv-head-row sub" }, modelControl(detail, isLive),
        el("span", { class: "conv-tagwrap" }, el("span", { class: "faint small" }, "tags"), tagsRow)),
      caps);
  }

  function filePicker() {
    const input = el("input", { type: "file", multiple: true, hidden: true });
    const chips = el("span", { class: "attach-chips" });
    const btn = el("button", { class: "btn small", onclick: () => input.click() }, "📎 attach");
    let pending = [];
    const renderChips = () => {
      chips.replaceChildren(...pending.map((f, i) =>
        el("span", { class: "attach-chip removable", title: "click to remove",
          onclick: () => { pending.splice(i, 1); renderChips(); } }, f.name, " ×")));
    };
    const addFiles = (list) => {
      for (const f of list) {
        // a pasted screenshot arrives as a nameless/generic blob — give it a real name
        const name = f.name && f.name !== "image.png" ? f.name
          : `pasted-${Date.now()}.${(f.type.split("/")[1] || "png").replace("+xml", "")}`;
        pending.push(new File([f], name, { type: f.type }));
      }
      renderChips();
    };
    input.onchange = () => { addFiles([...input.files]); input.value = ""; };
    // Ctrl/Cmd-V straight into the message box: clipboard files (screenshots, copied
    // images/documents) become attachments; plain text pastes stay untouched.
    const wirePaste = (target) => target.addEventListener("paste", (e) => {
      const files = [...(e.clipboardData?.files || [])];
      if (!files.length) return;
      e.preventDefault();
      addFiles(files);
    });
    return {
      picker: el("span", { class: "row", style: "gap:6px" }, btn, input, chips),
      files: () => [...pending],
      clearFiles: () => { pending = []; input.value = ""; renderChips(); },
      wirePaste,
    };
  }
}
