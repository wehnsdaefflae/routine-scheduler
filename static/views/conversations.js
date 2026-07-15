// Conversations tab, chat-first: the center pane is the conversation; everything else is
// a COLLAPSIBLE side pane. Left: a dense one-line-per-conversation list (state dot, title,
// time — details live in a hover card, tags collapse into one filter select). Right: the
// artifact panel (components/artifacts.js), wide enough to actually read a deliverable.
// Both panes fold to a slim rail (persisted), giving the chat or an artifact the room.
// A conversation is one continuous run: sending into a live reply injects; sending into a
// finished one resumes it in place, so the view remounts its tail after every send.

import { api, apiUpload } from "/static/api.js";
import { navigate } from "/static/router.js";
import { liveTail } from "/static/stream.js";
import { forgetField } from "/static/formpersist.js";
import { createChat } from "/static/components/chat.js";
import { createArtifacts } from "/static/components/artifacts.js";
import { createStateGraph } from "/static/components/stategraph.js";
import { createTaskTree } from "/static/components/tasktree.js";
import { permissionsPanel } from "/static/components/permissions.js";
import { busy, chip, el, emptyState, relTime, storage, tagChip, toast } from "/static/util.js";
import { followScroll } from "/static/follow.js";
import { enabled as notifyEnabled } from "/static/notify.js";
import { TERMINAL, WORKING } from "/static/states.js";

const PREFILL_KEY = "conv-new-prefill";

export async function render(view, slug, _query = {}) {
  view.classList.add("conv-view");
  const sideList = el("div", { class: "conv-list" });
  const tagSel = el("select", { class: "conv-tagsel", "data-nopersist": "", title: "filter by tag" });
  const search = el("input", { type: "search", placeholder: "search…" });
  const newBtn = el("a", { class: "btn primary small", href: "#/conversations" }, "+ new");
  const sideBody = el("div", { class: "pane-body" },
    el("div", { class: "row", style: "gap:6px" }, search, newBtn),
    el("div", { class: "row", style: "gap:6px" }, tagSel), sideList);
  const main = el("section", { class: "conv-main" });
  const artBody = el("div", { class: "pane-body" });

  // Pane chrome: widths (drag handles) AND a collapsed state both persist — either pane
  // folds to a slim rail so the chat or an open artifact gets the full width.
  const widths = { side: 250, art: 420, ...JSON.parse(storage.get("conv-pane-widths") || "{}") };
  if (widths.art < 380) widths.art = 420;   // the old default was too small to read anything
  const collapsed = { side: false, art: false,
                      ...JSON.parse(storage.get("conv-pane-collapsed") || "{}") };
  const saveCollapsed = () => storage.set("conv-pane-collapsed", JSON.stringify(collapsed));

  const pane = (which, title, cls, body) => {
    const fold = el("button", { class: "pane-fold", title: `collapse the ${title} pane` }, "◂▸");
    fold.onclick = () => { collapsed[which] = true; saveCollapsed(); applyWidths(); };
    const rail = el("button", { class: "pane-rail", title: `expand the ${title} pane` },
      el("span", { class: "pane-rail-label" }, title));
    rail.onclick = () => { collapsed[which] = false; saveCollapsed(); applyWidths(); };
    const cap = el("div", { class: "pane-cap" },
      el("span", { class: "pane-cap-title" }, title), fold);
    const node = el("aside", { class: cls }, cap, body, rail);
    return { node, rail };
  };
  const sideP = pane("side", "conversations", "conv-side", sideBody);
  const artP = pane("art", "artifacts", "conv-art", artBody);
  const side = sideP.node;
  const art = artP.node;
  art.hidden = !slug;

  const layout = el("div", { class: "conv-layout" });
  const applyWidths = () => {
    if (window.innerWidth <= 1100) {   // stacked responsive layout: rails make no sense
      layout.style.gridTemplateColumns = "";
      side.classList.remove("collapsed");
      art.classList.remove("collapsed");
      return;
    }
    side.classList.toggle("collapsed", collapsed.side);
    art.classList.toggle("collapsed", collapsed.art);
    const sideCol = collapsed.side ? "34px" : `${widths.side}px`;
    const artCol = !slug ? "0" : collapsed.art ? "34px" : `${widths.art}px`;
    layout.style.gridTemplateColumns =
      `${sideCol} ${collapsed.side ? 0 : 5}px minmax(0,1fr) `
      + `${!slug || collapsed.art ? 0 : 5}px ${artCol}`;
    // the handles must STAY in the grid (display:none would shift every pane one column
    // left, squeezing the chat into a 0px track) — a collapsed handle just goes inert
    handleL.classList.toggle("off", collapsed.side);
    handleR.classList.toggle("off", !slug || collapsed.art);
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

  // One shared hover card: the list rows stay one line each; snippet, tags, and state
  // detail appear beside the row on hover instead of costing permanent vertical space.
  let hover = null, hoverHideT = 0;
  const hideHover = () => { clearTimeout(hoverHideT); if (hover) hover.hidden = true; };
  function showHover(row, it) {
    if (!hover) {
      hover = el("div", { class: "conv-hover", hidden: true });
      view.append(hover);
    }
    clearTimeout(hoverHideT);
    const when = it.updated ? relTime(it.updated) : "no replies yet";
    hover.replaceChildren(...[
      el("div", { class: "conv-hover-title" }, it.title || it.slug),
      el("div", { class: "faint small" },
        `${it.state} · ${when}${it.turns ? ` · ${it.turns} turns` : ""}`),
      it.snippet ? el("div", { class: "conv-hover-snippet" }, it.snippet) : null,
      it.tags?.length ? el("div", { class: "conv-hover-tags" },
        ...it.tags.map((t) => el("span", { class: "tag" }, t))) : null,
      it.question ? el("div", { class: "small", style: "color:var(--warn)" }, "❓ waiting for you") : null,
    ].filter(Boolean));
    hover.hidden = false;
    const r = row.getBoundingClientRect();
    hover.style.left = Math.min(r.right + 8, window.innerWidth - 300) + "px";
    hover.style.top = Math.min(r.top, window.innerHeight - hover.offsetHeight - 12) + "px";
  }

  function renderList() {
    const q = search.value.trim().toLowerCase();
    const tags = [...new Set(items.flatMap((i) => i.tags || []))].sort();
    tagSel.replaceChildren(
      el("option", { value: "" }, "all tags"),
      ...tags.map((t) => el("option", { value: t, ...(t === activeTag ? { selected: true } : {}) }, t)));
    tagSel.hidden = !tags.length;
    const shown = items.filter((i) =>
      (!activeTag || (i.tags || []).includes(activeTag))
      && (!q || `${i.title} ${i.snippet} ${(i.tags || []).join(" ")}`.toLowerCase().includes(q)));
    sideList.replaceChildren();
    hideHover();
    if (!shown.length) {
      sideList.append(emptyState("💬", items.length ? "No matches" : "No conversations yet",
        items.length ? "" : "Start one — the first message is the task."));
      return;
    }
    for (const it of shown) {
      const row = el("a", {
        class: `conv-item${it.slug === slug ? " on" : ""}`,
        href: `#/conversations/${it.slug}` },
        el("span", { class: `dot ${it.state}` }),
        el("span", { class: "conv-title" }, it.title || it.slug),
        it.question ? el("span", { class: "conv-q", title: "waiting for you" }, "❓") : null,
        el("span", { class: "conv-when" }, relTime(it.updated)));
      row.addEventListener("mouseenter", () => showHover(row, it));
      row.addEventListener("mouseleave",
        () => { hoverHideT = setTimeout(() => { if (hover) hover.hidden = true; }, 120); });
      sideList.append(row);
    }
  }
  search.oninput = renderList;
  tagSel.onchange = () => { activeTag = tagSel.value; renderList(); };

  // ---- the center pane ------------------------------------------------------------------------
  const unmount = () => { for (const fn of cleanup.splice(0)) { try { fn(); } catch { /* gone */ } } };

  if (!slug) mountComposerOnly();
  else await mountConversation();

  loadList();
  const listTimer = setInterval(loadList, 20000);
  const onBus = (e) => {
    const ev = e.detail || {};
    if (ev.event === "run_finished" || ev.event === "run_started") loadList();
    // same opt-in as every other tier-1 notification (Settings → Notifications)
    if (ev.event === "run_finished" && ev.routine === slug && document.hidden && notifyEnabled()) {
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
    // Playbook picker (the use-instruction analog): a picked playbook's brief seeds the
    // conversation; the first-message box then just SPECIALIZES it, and may be left empty.
    const pbSel = el("select", { "data-nopersist": "" },
      el("option", { value: "" }, "no playbook · start fresh"));
    const pbHint = el("div", { class: "faint small" });
    let pbList = [];
    api("/api/playbooks").then((r) => {
      pbList = r.playbooks || [];
      pbList.forEach((p) => pbSel.append(el("option", { value: p.slug }, p.title || p.slug)));
    }).catch(() => { /* library unreachable — picker stays empty, plain conversation still works */ });
    pbSel.onchange = () => {
      const p = pbList.find((x) => x.slug === pbSel.value);
      pbHint.textContent = p ? `▸ ${p.when || ""}${p.axis ? `  ·  varies: ${p.axis}` : ""}` : "";
      text.placeholder = pbSel.value
        ? "Optional — anything specific for this run? The playbook is the brief…"
        : "What should the agent do? The first message becomes the conversation's task…";
    };
    const workdir = el("input", { type: "text", placeholder: "~/path/to/project (optional)" });
    // Pre-start budgets: turns per REPLY, and a cumulative cap over the WHOLE conversation
    // (both optional — blank keeps the default; -1 = unlimited).
    const turnsIn = el("input", { type: "number", min: "-1", step: "1", placeholder: "10",
      style: "width:80px", title: "max turns per reply (-1 = unlimited)" });
    const totalTurnsIn = el("input", { type: "number", min: "-1", step: "1", placeholder: "∞",
      style: "width:80px", title: "max turns for the whole conversation (blank or -1 = unlimited)" });
    // Pre-start model picker: pick a catalog model by NAME (or fall back to the system model),
    // so a conversation can start on the right model instead of system-default-then-switch.
    const modelSel = el("select", { "data-nopersist": "" },
      el("option", { value: "" }, "default · system model"));
    api("/api/settings/models").then((r) => {
      if (r.system_model) modelSel.options[0].textContent = `default · ${r.system_model}`;
      (r.models || []).forEach((m) => modelSel.append(el("option", { value: m.name }, m.name)));
    }).catch(() => { /* settings unreachable — the default option still works */ });
    const shellChk = el("input", { type: "checkbox" });
    const { picker, files, clearFiles, wirePaste } = filePicker();
    wirePaste(text);
    const send = el("button", { class: "btn primary" }, "start conversation");
    send.onclick = async () => {
      if (!text.value.trim() && !pbSel.value) { toast("write the first message or pick a playbook"); return; }
      send.disabled = true;
      try {
        const fd = new FormData();
        fd.append("text", text.value);
        if (pbSel.value) fd.append("playbook", pbSel.value);
        if (modelSel.value) fd.append("model", modelSel.value);
        if (workdir.value.trim()) fd.append("workdir", workdir.value.trim());
        if (turnsIn.value.trim()) fd.append("max_turns", turnsIn.value.trim());
        if (totalTurnsIn.value.trim()) fd.append("max_total_turns", totalTurnsIn.value.trim());
        if (shellChk.checked) fd.append("shell", "1");
        for (const f of files()) fd.append("files", f);
        const r = await apiUpload("/api/conversations", fd);
        forgetField(text); forgetField(workdir);   // submitted — never refill the next composer
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
        el("div", { class: "row mt", style: "gap:8px;align-items:center;flex-wrap:wrap" },
          el("span", { class: "faint small" }, "playbook"), pbSel),
        pbHint,
        el("div", { class: "row mt", style: "gap:8px;flex-wrap:wrap" }, picker, send),
        el("div", { class: "row mt", style: "gap:8px;align-items:center" },
          el("span", { class: "faint small" }, "model"), modelSel),
        el("div", { class: "row mt", style: "gap:12px;align-items:center;flex-wrap:wrap" },
          el("span", { class: "faint small" }, "budget"),
          el("label", { class: "faint small row", style: "gap:4px;align-items:center" },
            "turns / reply", turnsIn),
          el("label", { class: "faint small row", style: "gap:4px;align-items:center" },
            "whole conversation", totalTurnsIn)),
        el("details", { class: "mt small" },
          el("summary", { style: "cursor:pointer;color:var(--muted)" }, "⚙ options: project dir, shell"),
          el("div", { class: "conv-opts" },
            el("label", {}, "project directory — the agent may read & edit it", workdir),
            el("label", { class: "row", style: "gap:8px" }, shellChk,
              el("span", {}, "allow shell commands (the escape hatch — off by default)")))),
        el("div", { class: "faint small mt" },
          "pick a model above or start on the system default — switch it any time at the top of the conversation")));
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

    artBody.replaceChildren();
    // the state graph rides at the top of the artifact rail: current phase lit up,
    // re-highlighted live on the SSE state events below
    const graphBody = el("div", {});
    const treeBody = el("div", {});
    artBody.append(el("div", { class: "rail-cap" }, "state"), graphBody);
    if (detail.run_id) artBody.append(el("div", { class: "rail-cap" }, "tasks"), treeBody);
    // detached background tasks the assistant launched (the `detach` action): a flat cross-run
    // list with a cancel affordance. Hidden until there is at least one.
    const bgCap = el("div", { class: "rail-cap", hidden: true }, "background");
    const bgBody = el("div", { class: "bg-tasks" });
    artBody.append(bgCap, bgBody);
    artBody.append(el("div", { class: "rail-cap" }, "artifacts"));
    const stateGraph = createStateGraph(graphBody, {
      graphUrl: `/api/conversations/${slug}/stategraph` });
    const taskTree = detail.run_id ? createTaskTree(treeBody, {
      treeUrl: `/api/runs/${detail.run_id}/tree`, isLive: () => !TERMINAL.has(curState) }) : null;
    const artifacts = createArtifacts(artBody, { slug });

    const BG_LIVE = new Set(["queued", "starting", "running", "waiting_user", "paused"]);
    function paintBackground(rows) {
      bgBody.replaceChildren();
      bgCap.hidden = !rows.length;
      for (const t of rows) {
        const row = el("div", { class: "bg-task" },
          chip(t.state, t.state),
          el("span", { class: "bg-task-label", title: t.summary || "" }, t.label));
        if (BG_LIVE.has(t.state)) {
          const btn = el("button", { class: "bg-cancel", title: "cancel this background task" }, "✕");
          btn.onclick = async () => {
            btn.disabled = true;
            try { await api(`/api/conversations/${slug}/background/${t.taskid}/cancel`, { method: "POST" }); }
            catch (e) { toast(e.message); btn.disabled = false; return; }
            toast("cancelling background task…");
            setTimeout(refreshBackground, 800);
          };
          row.append(btn);
        }
        bgBody.append(row);
      }
    }
    async function refreshBackground() {
      try { paintBackground(await api(`/api/conversations/${slug}/background`)); } catch { /* transient */ }
    }
    paintBackground(detail.background || []);
    const bgTimer = setInterval(refreshBackground, 15000);
    cleanup.push(() => { clearInterval(bgTimer); artifacts.destroy(); taskTree?.stop(); });

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
      // the conversation state diagram lights the reply-cycle node from the live run state
      // (a conversation loops — it has no single workflow phase to highlight)
      stateGraph.setPhase(WORKING.has(s) ? "working" : "waiting for you");
      composer.setLive(!TERMINAL.has(s));
      if (TERMINAL.has(s)) { chat.finishOpenFold(); artifacts.refresh(); taskTree?.refresh(); }
      refreshBackground();   // a finished detached task wakes the conversation → catch it here
    };
    setState(detail.state);

    if (!detail.run_id) return;   // created but never fired (shouldn't happen)
    const tail = liveTail({
      page: (o) => `/api/runs/${detail.run_id}/transcript?offset=${o}`,
      events: (o) => `/api/runs/${detail.run_id}/events?offset=${o}`,
      offset: 0,
      onEvent: (ev) => { chat.add(ev); scrollDown();
                         if (ev.type === "subrun_start" || ev.type === "subrun_end") taskTree?.refresh(); },
      onState: (s) => { setState(s.state);   // setState re-lights the state diagram
                        showQuestion(questionBox, s.question); },
      onGone: () => setState("finished"),
    });
    cleanup.push(() => tail.stop());

    // scrolling up pauses follow; back to the bottom resumes (same rule as the run view)
    cleanup.push(followScroll({
      pause: () => { autoscroll = false; },
      resume: () => { autoscroll = true; },
    }));

    function buildComposer() {
      const input = el("textarea", { rows: 2, placeholder: "message…" });
      const { picker, files, clearFiles, wirePaste } = filePicker();
      wirePaste(input);
      const send = el("button", { class: "btn primary" }, "send");
      // Save this conversation as a reusable playbook (the save-instruction analog); when it was
      // itself seeded from a playbook, also offer to fold these deltas back into that playbook.
      const savePb = el("button", { class: "btn small ghost",
        title: "distil this conversation into a reusable playbook" }, "＋ save as playbook");
      savePb.onclick = async () => {
        savePb.disabled = true;
        toast("distilling a playbook — a few seconds…");
        try {
          const r = await api(`/api/conversations/${slug}/playbook`, { method: "POST" });
          toast(`saved playbook “${r.slug}”${r.axis ? `  ·  varies: ${r.axis}` : ""}`, 6000);
        } catch (err) { toast(err.message, 6000, { error: true }); }
        savePb.disabled = false;
      };
      const pbRow = el("div", { class: "row", style: "gap:8px;margin-top:6px;flex-wrap:wrap" }, savePb);
      if (detail.playbook) {
        const updPb = el("button", { class: "btn small ghost",
          title: `revise the “${detail.playbook}” playbook from this conversation` },
          `⟳ update playbook: ${detail.playbook}`);
        updPb.onclick = async () => {
          updPb.disabled = true;
          toast("revising the playbook…");
          try {
            const r = await api(`/api/conversations/${slug}/playbook`, { method: "PUT" });
            toast(`updated playbook “${r.slug}”`, 6000);
          } catch (err) { toast(err.message, 6000, { error: true }); }
          updPb.disabled = false;
        };
        pbRow.append(updPb);
      }
      // Manual stop for a live reply — the run has no automatic backstop when its budgets
      // are set to -1 (unlimited), so the user must be able to end it at any time. Aborts at
      // the next turn boundary (the reply finishes as `aborted`, transcript + LEDGER intact).
      const stopBtn = el("button", { class: "btn small danger",
        title: "stop this reply now — it aborts at the next turn boundary" }, "✕ stop");
      stopBtn.hidden = true;
      stopBtn.onclick = async () => {
        stopBtn.disabled = true;
        try { await api(`/api/runs/${detail.run_id}/abort`, { method: "POST" }); toast("stopping the reply…"); }
        catch (err) { toast(err.message, 4000, { error: true }); stopBtn.disabled = false; }
      };
      const node = el("div", { class: "conv-composer" }, input,
        el("div", { class: "row", style: "gap:8px" }, picker, send, stopBtn), pbRow);
      const submit = async () => {
        if (!input.value.trim()) return;
        send.disabled = true;
        try {
          const fd = new FormData();
          fd.append("text", input.value);
          for (const f of files()) fd.append("files", f);
          const r = await apiUpload(`/api/conversations/${slug}/message`, fd);
          input.value = "";
          forgetField(input);   // sent — the draft must not refill on reload
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
      return { node, setLive: (live) => { stopBtn.hidden = !live; if (live) stopBtn.disabled = false; } };
    }
  }

  function showQuestion(box, q) {
    box.replaceChildren();
    if (!q) return;
    // data-persist keyed by qid: this question's draft is its own — never another's.
    const input = el("textarea", { rows: 1, placeholder: "your answer… (Shift+Enter for a new line)",
      "data-persist": `answer-${q.qid}`, style: "flex:1;resize:vertical" });
    const send = el("button", { class: "btn primary" }, "answer");
    const discuss = el("button", { class: "btn",
      title: "send as a follow-up question / thought — the model replies and the question stays open" },
      "ask back");
    const submit = async (intermediate) => {
      if (!input.value.trim()) return;
      try {
        await api(`/api/questions/${q.qid}/answer`, { method: "POST",
          body: { text: input.value, intermediate } });
        forgetField(input);   // answered — the draft must never refill
        toast(intermediate ? "sent — the model will reply and re-ask" : "answer sent");
        box.replaceChildren();
      } catch (err) { toast(err.message, 4000, { error: true }); }
    };
    send.onclick = () => submit(false);
    discuss.onclick = () => submit(true);
    input.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(false); } };
    box.append(el("div", { class: "panel warn mt" },
      el("div", { class: "prose" }, "❓ ", q.question || ""),
      q.default ? el("div", { class: "faint small mt" }, `↪ without an answer: ${q.default}`) : null,
      q.options?.length ? el("div", { class: "row mt" },
        q.options.map((o) => el("button", { class: "btn small", onclick: () => { input.value = o; } }, o))) : null,
      el("div", { class: "row mt" }, input, send, discuss)));
  }

  // The model line at the top of a conversation: shows the EFFECTIVE model (override or
  // system default) and switches it at any point — routine.yaml is patched (each reply
  // boots on it), and a live reply additionally gets the mid-run control.json switch.
  function modelControl(detail, isLive) {
    const cur = detail.models?.main || "";        // a catalog model NAME, or "" = system default
    const sysLabel = detail.system_model || "system model";
    const sel = el("select", { style: "width:auto;font-size:11.5px;padding:3px 6px" },
      el("option", { value: "" }, `default · ${sysLabel}`),
      (detail.catalog || []).map((n) =>
        el("option", { value: n, selected: cur === n || null }, n)));
    const apply = el("button", { class: "btn small primary", hidden: true }, "apply");
    sel.onchange = () => { apply.hidden = false; };
    apply.onclick = async () => {
      const name = sel.value;
      const models = name ? { main: name, subroutine: name, tool_call: name } : {};
      try {
        await api(`/api/conversations/${slug}`, { method: "PATCH", body: { models } });
        if (name && isLive() && detail.run_id) {
          // the current reply switches its main too, at its next turn boundary
          await api(`/api/runs/${detail.run_id}/model`,
            { method: "POST", body: { model: name } }).catch(() => {});
        }
        toast(name ? `model → ${name}` : `model → ${sysLabel}`);
        apply.hidden = true;
      } catch (err) { toast(err.message, 4000, { error: true }); }
    };
    return el("span", { class: "conv-model" },
      el("span", { class: "faint small" }, "model"), sel, apply);
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
    const numIn = (v, min = "1") => el("input", { type: "number", min, value: v,
      style: "width:90px;font-size:11.5px;padding:3px 6px" });
    const turnsIn = numIn(b.max_turns ?? 10);
    const minsIn = numIn(b.max_wall_clock_min ?? 30, "-1");    // -1 = unlimited time
    const tokIn = numIn(b.max_total_tokens ?? 400000, "-1");   // -1 = unlimited tokens
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
      budgetField("turns / reply", turnsIn), budgetField("minutes / reply (-1=∞)", minsIn),
      budgetField("tokens / reply (-1=∞)", tokIn), saveBudgets));
    capBody.append(permissionsPanel(detail.permissions, detail.capabilities, {
      disableRuns: "a conversation is one continuous run — previous-run depth is routine-only",
      saveLabel: "save permissions",
      onSave: async (payload) => {
        try {
          await api(`/api/conversations/${slug}/permissions`, { method: "PUT", body: payload });
          toast("permissions saved — they apply from the next reply");
        } catch (err) { toast(err.message, 4000, { error: true }); }
      },
    }));
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
