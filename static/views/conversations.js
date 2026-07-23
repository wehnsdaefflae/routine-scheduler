// Conversations tab, chat-first, in the RUN-PAGE layout: the conversation owns the full
// main column; everything else lives in margin rails (the run view's .run-rail pattern).
// Left rail: a dense one-line-per-conversation list (state dot, title, time — details in a
// hover card, tags collapse into one filter select). Right rail: state graph, task tree,
// and the artifact panel. On wide screens the rails park FIXED in the viewport margins
// beside the 1180px column; otherwise they are ordinary collapsible blocks above the chat.
// A conversation is one continuous run: sending into a live reply injects; sending into a
// finished one resumes it in place, so the view remounts its tail after every send.

import { referChip } from "/static/components/referchip.js";
import { filePicker } from "/static/components/filepicker.js";
import { mountComposerOnly, PREFILL_KEY } from "/static/views/conversations-new.js";
import { renderHead } from "/static/views/conversations-head.js";
import { api, apiUpload } from "/static/api.js";
import { questionPanel } from "/static/components/answerform.js";
import { navigate } from "/static/router.js";
import { liveTail } from "/static/stream.js";
import { forgetField } from "/static/formpersist.js";
import { createChat } from "/static/components/chat.js";
import { createArtifacts } from "/static/components/artifacts.js";
import { createFileActivity } from "/static/components/fileactivity.js";
import { createStateGraph } from "/static/components/stategraph.js";
import { createTaskTree } from "/static/components/tasktree.js";
import { busy, chip, el, emptyState, relTime, toast } from "/static/util.js";
import { followScroll } from "/static/follow.js";
import { enabled as notifyEnabled } from "/static/notify.js";
import { TERMINAL, WORKING } from "/static/states.js";

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

  // Run-page layout (the run view's .run-rail pattern): the chat owns the main column and
  // the rails PERSIST at every desktop width (user order 2026-07-16) — the conversation
  // list always LEFT, state/tasks/artifacts always RIGHT. ≥1560px they park fixed in the
  // viewport margins; 1200–1559px they become sticky grid columns beside the chat (CSS);
  // only below 1200px do they stack (list above the chat, artifacts below).
  const sideRail = el("details", { class: "run-rail left", open: true },
    el("summary", { class: "small" }, "conversations"), sideBody);
  const artRail = el("details", { class: "run-rail", open: true },
    el("summary", { class: "small" }, "state & artifacts"), artBody);
  artRail.hidden = !slug;
  view.append(sideRail, main, artRail);

  let items = [], activeTag = "";
  let cleanup = [];   // per-mount teardowns (tail, timers, artifact blobs)

  // ---- sidebar --------------------------------------------------------------------------------
  async function loadList() {
    try { items = await api("/api/conversations"); }
    catch (err) {
      const retry = el("button", { class: "btn small", onclick: loadList }, "retry");
      sideList.replaceChildren(el("div", { class: "empty" },
        el("div", {}, `couldn't load conversations: ${err.message}`), retry));
      return;
    }
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
        el("span", { class: `dot ${it.state}`, role: "img", title: it.state,
          "aria-label": `state: ${it.state}` }),
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

  if (!slug) mountComposerOnly(main);
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
  return () => { unmount(); clearInterval(listTimer); window.removeEventListener("rsched-bus", onBus); };


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
    renderHead(head, detail, stateChip,
               { slug, isLive: () => !TERMINAL.has(curState), onListChanged: loadList });
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
    const filesBody = el("div", {});
    artBody.append(el("div", { class: "rail-cap" }, "state"), graphBody);
    if (detail.run_id) artBody.append(el("div", { class: "rail-cap" }, "tasks"), treeBody,
                                      el("div", { class: "rail-cap" }, "files"), filesBody);
    // detached background tasks the assistant launched (the `detach` action): a flat cross-run
    // list with a cancel affordance. Hidden until there is at least one.
    const bgCap = el("div", { class: "rail-cap", hidden: true }, "background");
    const bgBody = el("div", { class: "bg-tasks" });
    artBody.append(bgCap, bgBody);
    artBody.append(el("div", { class: "rail-cap" }, "artifacts"));
    const stateGraph = createStateGraph(graphBody, {
      graphUrl: `/api/conversations/${slug}/stategraph`,
      ...(detail.run_id ? { statsUrl: `/api/runs/${detail.run_id}/phases` } : {}) });
    const taskTree = detail.run_id ? createTaskTree(treeBody, {
      treeUrl: `/api/runs/${detail.run_id}/tree`, isLive: () => !TERMINAL.has(curState) }) : null;
    const fileActivity = detail.run_id
      ? createFileActivity(filesBody, { url: `/api/runs/${detail.run_id}/files` }) : null;
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
      onRefer: composer.setRef,
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
      if (TERMINAL.has(s)) { chat.finishOpenFold(); artifacts.refresh(); taskTree?.refresh();
                             fileActivity?.refresh(); }
      refreshBackground();   // a finished detached task wakes the conversation → catch it here
    };
    setState(detail.state);

    if (!detail.run_id) return;   // created but never fired (shouldn't happen)
    const tail = liveTail({
      page: (o) => `/api/runs/${detail.run_id}/transcript?offset=${o}`,
      events: (o) => `/api/runs/${detail.run_id}/events?offset=${o}`,
      offset: 0,
      onEvent: (ev) => { chat.add(ev); scrollDown();
                         if (ev.type === "subrun_start" || ev.type === "subrun_end") taskTree?.refresh();
                         if (ev.type === "observation" && ["read_file", "view_image", "write_file",
                             "edit_file"].includes(ev.payload?.kind)) fileActivity?.poke(); },
      onState: (s) => { setState(s.state);   // setState re-lights the state diagram
                        questionPanel(questionBox, s.question); },
      onGone: () => setState("finished"),
    });
    cleanup.push(() => tail.stop());

    // scrolling up pauses follow; back to the bottom resumes (same rule as the run view)
    cleanup.push(followScroll({
      pause: () => { autoscroll = false; },
      resume: () => { autoscroll = true; },
    }));

    function buildComposer() {
      const input = el("textarea", { rows: 2, placeholder: "message…  (Shift+Enter for a new line · / for commands)" });
      const { picker, files, clearFiles, wirePaste } = filePicker();
      wirePaste(input);
      const send = el("button", { class: "btn primary" }, "send");

      const ref = referChip(input);
      const refBar = ref.node;
      const setRef = ref.setRef;

      // ---- slash commands: the user runs the SAME actions/utils the assistant can ----
      let catalog = null;
      const loadCatalog = async () => {
        if (!catalog) {
          catalog = await api(`/api/conversations/${slug}/commands`)
            .catch(() => ({ kinds: [], utils: [] }));
        }
        return catalog;
      };
      // autocomplete dropdown, anchored above the input
      const suggests = el("div", { class: "cmd-suggest", hidden: true });
      let items = [];
      let selIdx = 0;
      function paintSuggest() {
        suggests.replaceChildren(...items.map((it, i) => el("div",
          { class: `cs-item${i === selIdx ? " on" : ""}`,
            onclick: () => { acceptSuggest(i); } },
          el("code", {}, it.label), el("span", { class: "cs-hint" }, it.hint || ""))));
        suggests.hidden = !items.length;
      }
      function acceptSuggest(i = selIdx) {
        input.value = items[i].insert;
        items = [];
        paintSuggest();
        input.focus();
        updateSuggest();
      }
      async function updateSuggest() {
        const v = input.value;
        if (!v.startsWith("/") || v.includes("\n")) { items = []; paintSuggest(); return; }
        await loadCatalog();
        const utilArg = v.match(/^\/util\s+(\S*)$/);
        if (utilArg) {
          items = catalog.utils.filter((u) => u.name.startsWith(utilArg[1]))
            .slice(0, 10).map((u) => ({ label: `/util ${u.name}`, hint: u.summary,
                                        insert: `/util ${u.name} ` }));
        } else if (!v.includes(" ")) {
          items = catalog.kinds.filter((k) => k.kind.startsWith(v.slice(1)))
            .map((k) => ({ label: k.usage, hint: k.summary, insert: `/${k.kind} ` }));
        } else items = [];
        selIdx = 0;
        paintSuggest();
      }
      input.addEventListener("input", () => { updateSuggest(); });
      // help panel: the full command + util reference, next to the input
      const helpPanel = el("div", { class: "cmd-help", hidden: true });
      const helpBtn = el("button", { class: "btn small ghost",
        title: "slash commands — run the same actions and utils the assistant uses" },
        "/ commands");
      helpBtn.onclick = async () => {
        if (helpPanel.hidden) {
          await loadCatalog();
          const row = (usage, summary) => el("div", { class: "cmd-row" },
            el("code", {}, usage), el("span", { class: "muted small" }, summary));
          helpPanel.replaceChildren(
            el("div", { class: "cmd-cap" },
              "actions — type / in the message box to autocomplete; the result shows here and the assistant sees it too"),
            ...catalog.kinds.map((k) => row(k.usage, k.summary)),
            el("div", { class: "cmd-cap" }, `global utils — /util <name> [args]`),
            ...catalog.utils.map((u) => row(u.usage || `/util ${u.name}`, u.summary)));
        }
        helpPanel.hidden = !helpPanel.hidden;
      };
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
      const node = el("div", { class: "conv-composer" }, helpPanel, refBar,
        el("div", { class: "cmd-anchor" }, suggests, input),
        el("div", { class: "row", style: "gap:8px" }, picker, helpBtn, send, stopBtn), pbRow);
      const submit = async () => {
        if (!input.value.trim()) return;
        send.disabled = true;
        try {
          const fd = new FormData();
          // a known /<kind> head marks the message as a COMMAND the engine executes
          const head = input.value.trimStart().match(/^\/([a-z_]+)/);
          const isCommand = Boolean(head
            && (await loadCatalog()).kinds.some((k) => k.kind === head[1]));
          // a primed reference rides the text as one leading quoted line (prose only — a
          // command must keep its /<kind> head)
          fd.append("text", ref.pending && !isCommand
            ? `> re ${ref.pending.label}: ${ref.pending.snippet}\n\n${input.value}`
            : input.value);
          if (isCommand) fd.append("command", "1");
          for (const f of files()) fd.append("files", f);
          const r = await apiUpload(`/api/conversations/${slug}/message`, fd);
          input.value = "";
          setRef(null);
          forgetField(input);   // sent — the draft must not refill on reload
          clearFiles();
          toast(r.command ? "command running — you keep the turn"
            : r.delivery === "mid-run" ? "delivered — picked up next turn"
            : "waking the conversation…");
          // reattach to show the result (command) or the live reply; mid-run streams already
          if (r.delivery !== "mid-run") setTimeout(mountConversation, 700);
        } catch (err) { toast(err.message, 5000, { error: true }); }
        send.disabled = false;
      };
      send.onclick = submit;
      input.onkeydown = (e) => {
        if (!suggests.hidden) {           // the dropdown owns the keys while it is open
          if (e.key === "ArrowDown") { e.preventDefault(); selIdx = Math.min(items.length - 1, selIdx + 1); paintSuggest(); return; }
          if (e.key === "ArrowUp") { e.preventDefault(); selIdx = Math.max(0, selIdx - 1); paintSuggest(); return; }
          if (e.key === "Tab" || e.key === "Enter") { e.preventDefault(); acceptSuggest(); return; }
          if (e.key === "Escape") { items = []; paintSuggest(); return; }
        }
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
      };
      return { node, setRef,
               setLive: (live) => { stopBtn.hidden = !live; if (live) stopBtn.disabled = false; } };
    }
  }


}
