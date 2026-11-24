// Conversations tab, chat-first, in the RUN-PAGE layout: the conversation owns the full
// main column; everything else lives in margin rails (the run view's .run-rail pattern).
// Left rail: a dense one-line-per-conversation list (state dot, title, time — details in a
// hover card, tags collapse into one filter select). Right rail: phase graph, sub-run tree,
// and the artifact panel. On wide screens the rails park FIXED in the viewport margins
// beside the 1180px column; otherwise they are ordinary collapsible blocks above the chat.
// A conversation is one continuous run: sending into a live reply injects; sending into a
// finished one resumes it in place, so the view remounts its tail after every send.

import { referChip } from "/static/components/referchip.js";
import { filePicker } from "/static/components/filepicker.js";
import { mountComposerOnly, prefillComposer } from "/static/views/conversations-new.js";
import { renderHead } from "/static/views/conversations-head.js";
import { createConversationList } from "/static/views/conversations-list.js";
import { api, apiUpload } from "/static/api.js";
import { questionPanel } from "/static/components/answerform.js";
import { navigate } from "/static/router.js";
import { liveTail } from "/static/stream.js";
import { forgetField } from "/static/formpersist.js";
import { branchSectionBody } from "/static/components/branch-rail.js";
import { buildConversationRail } from "/static/views/conversation-rail.js";
import { familyOf, forkAt, rewindTo } from "/static/components/branches.js";
import { flagRefusal } from "/static/components/refusalflag.js";
import { finalOffer, playbookRow } from "/static/components/playbook-row.js";
import { createChat, typedBody, userEcho } from "/static/components/chat.js";
import { backgroundButton } from "/static/components/bg-start.js";
import { adminToggle } from "/static/components/admintoggle.js";
import { busy, chip, el, emptyState, relTime, toast, toastError } from "/static/util.js";
import { wireRunRail } from "/static/resizable.js";
import { followScroll } from "/static/follow.js";
import { show as notifyShow } from "/static/notify.js";
import { TERMINAL, WORKING } from "/static/states.js";

export async function render(view, slug, _query = {}) {
  view.classList.add("conv-view");
  const sideBody = el("div", { class: "pane-body" });
  const main = el("section", { class: "conv-main" });
  const artBody = el("div", { class: "pane-body" });

  // Run-page layout (the run view's .run-rail pattern): the chat owns the main column and
  // the rails PERSIST at every desktop width (user order 2026-07-16) — the conversation
  // list always LEFT, state/tasks/artifacts always RIGHT. ≥1900px they park fixed in the
  // viewport margins; 1100–1899px they become sticky grid columns beside the chat (CSS);
  // only below 1100px do they stack (list above the chat, artifacts below). Both are
  // drag-resizable and hideable in either wide mode (F441, static/resizable.js).
  const sideRail = el("details", { class: "run-rail left", open: true },
    el("summary", { class: "small" }, "conversations"), sideBody);
  const artRail = el("details", { class: "run-rail", open: true },
    el("summary", { class: "small" }, "phases & artifacts"), artBody);
  artRail.hidden = !slug;
  view.append(sideRail, main, artRail);
  wireRunRail(sideRail, "left");
  wireRunRail(artRail, "right");

  let cleanup = [];   // per-mount teardowns (tail, timers, artifact blobs)
  // Set by the view's teardown. A remount can still be on its way when the view goes — the
  // one 700 ms after a send, or one already awaiting its detail read — and whatever it armed
  // after the teardown had run (a live tail, the rail polls) would never be stopped.
  let disposed = false;
  // F295: the optimistic echo of a just-sent message. A post-finish send has NO transcript
  // event until the woken leg boots, and the view remounts ~700ms after every send — so the
  // echo lives here (render scope), is re-appended by each mount, and is dropped only when
  // the matching user_injection event arrives.
  let pendingEcho = null;   // { text, node, id }

  // D139: the echo bubble's controls. A queued message is the user's until the model reads
  // it, so the bubble that says "sent" is also where it is revised or withdrawn. Rebuilding
  // the bubble after each act keeps ONE construction path for both the send and the remount.
  function echoNode() {
    if (!pendingEcho) return null;
    const ctl = !pendingEcho.id ? undefined : {
      onRevise: async (text) => {
        if (!text.trim()) return;
        try {
          await api(`/api/conversations/${slug}/messages/${pendingEcho.id}`,
                    { method: "PUT", body: { text } });   // api() stringifies for us
          pendingEcho.text = text;
          repaintEcho();
          toast("revised — the model reads this version");
        } catch (err) { toastError(err, 5000); repaintEcho(); }
      },
      onWithdraw: async () => {
        try {
          await api(`/api/conversations/${slug}/messages/${pendingEcho.id}`,
                    { method: "DELETE" });
          pendingEcho.node?.remove();
          pendingEcho = null;
          toast("withdrawn — the model never sees it");
        } catch (err) { toastError(err, 5000); repaintEcho(); }
      },
      onCancel: () => repaintEcho(),
    };
    return userEcho(pendingEcho.text, ctl);
  }

  function repaintEcho() {
    if (!pendingEcho) return;
    const fresh = echoNode();
    pendingEcho.node?.replaceWith(fresh);
    pendingEcho.node = fresh;
  }

  // ---- sidebar --------------------------------------------------------------------------------
  // Its own module (views/conversations-list.js): which conversations exist is read and changed
  // separately from the one being read, and this file is at its size ceiling.
  const sideHost = createConversationList(sideBody, { view, onCurrent: () => slug });
  const loadList = sideHost.loadList;


  // ---- the center pane ------------------------------------------------------------------------
  const unmount = () => { for (const fn of cleanup.splice(0)) { try { fn(); } catch { /* gone */ } } };

  await loadList();   // FIRST: its rows are the head's branch family (D306 pt 5, familyOf)
  if (!slug) mountComposerOnly(main);
  else await mountConversation();
  const listTimer = setInterval(loadList, 20000);
  const onBus = (e) => {
    const ev = e.detail || {};
    if (ev.event === "run_finished" || ev.event === "run_started") loadList();
    // same opt-in as every other tier-1 notification (Settings → Notifications), through the
    // one owner of the tag/click convention — so three open tabs raise ONE notification and
    // clicking it lands on this conversation
    if (ev.event === "run_finished" && ev.routine === slug && document.hidden) {
      notifyShow("conversation reply ready", (ev.summary || "").slice(0, 120),
                 { tag: `rsched-reply-${slug}`, href: `#/conversations/${slug}` });
    }
  };
  window.addEventListener("rsched-bus", onBus);
  return () => {
    disposed = true;
    unmount(); clearInterval(listTimer); window.removeEventListener("rsched-bus", onBus);
  };


  // ---- an existing conversation -----------------------------------------------------------------
  async function mountConversation() {
    if (disposed) return;
    unmount();
    let detail;
    try { detail = await api(`/api/conversations/${slug}`); }
    catch (err) {
      main.replaceChildren(emptyState("✕", "Conversation not found", err.message));
      return;
    }
    if (disposed) return;
    // The live tail, assigned once the run id is known (below). The composer closes over it:
    // a send RESUMES this run in place, so the tail re-attaches instead of the page rebuilding.
    let tail = null;
    // Declared here rather than beside the tail: the rail sections below read it to decide
    // whether there is anything worth polling for.
    let curState = detail.state;
    const stateChip = chip(detail.state, detail.state);
    const head = el("div", { class: "conv-head" });
    // D306 pt 2: the rail's BRANCHES body, made before the head because the head's branch bar
    // fills it from the lineage read it already makes — one read, two surfaces, no second poll.
    const branchBody = branchSectionBody();
    const branchBar = renderHead(head, detail, stateChip,
               { slug, isLive: () => !TERMINAL.has(curState), onListChanged: loadList, railBody: branchBody,
                 family: () => familyOf(sideHost.rows(), slug) });   // pt 5: no /lineage with no family
    const chatBox = el("div", { class: "conv-chat" });
    // the echo box sits AFTER the chat so the pending bubble always renders below the
    // transcript history, which streams in asynchronously (F295)
    const echoBox = el("div", { class: "chat-echo" });
    const pbFinal = finalOffer(slug, detail), waiting = el("div", {}), questionBox = el("div", {});
    const composer = buildComposer();
    main.replaceChildren(head, chatBox, pbFinal.node, echoBox, waiting, questionBox, composer.node);
    if (pendingEcho) { pendingEcho.node = echoNode(); echoBox.append(pendingEcho.node); }

    // Every rail section, the component behind each and their teardown, in one module
    // (views/conversation-rail.js): which sections exist and what fills them is one
    // responsibility, and this view's own is the reply cycle.
    const { phaseGraph, subRunTree, fileActivity, artifacts, scripts, bgTasks, stop: stopRail } =
      buildConversationRail(artBody, { slug, detail, branchBody,
        isLive: () => !TERMINAL.has(curState), family: familyOf(sideHost.rows(), slug) });
    cleanup.push(stopRail);

    const chat = createChat(chatBox, {
      answer: (qid, text, decision) => api(`/api/questions/${qid}/answer`, { method: "POST", body: decision ? { decision } : { text } }),
      loadSub: (n, o) => api(`/api/runs/${detail.run_id}/transcript?sub=${n}&offset=${o}`),
      isLive: () => !TERMINAL.has(curState), slug,   // F591: a reminder id links to its row
      onArtifact: () => artifacts.poke(), openArtifact: (p) => artifacts.openPath(p),   // F891: coalesce the replay's write-per-event storm; a hand-back card's file links open the panel
      onFork: (_title, lastUserText) => {
        prefillComposer(lastUserText);
        navigate("#/conversations");
      },
      // R1006: fork AT the clicked reply, so the turn is never typed. D306 pt 4: the control
      // opens the fork dialog (name + optional first instruction); `name` prefills it.
      onBranch: (turn) => forkAt(slug, turn, { isLive: () => !TERMINAL.has(curState), name: detail.title || slug }),
      onRewind: (turn) => rewindTo(slug, detail.run_id, turn, { isLive: () => !TERMINAL.has(curState) }),
      // ⚑ (operator decision 2026-10-01): redo a refused reply on the uncensored model, which
      // then carries the conversation — the pick, when it has none, from its own catalog
      onFlag: (reply) => flagRefusal(detail.run_id, reply, { isLive: () => !TERMINAL.has(curState),
        catalog: detail.catalog || [], catalogMeta: detail.catalog_meta || {} }),
      onRefer: composer.setRef,
      // message attachments render inline (attachments/ is a servable subdir of the route)
      fileUrl: (rel) => `/api/conversations/${slug}/file?path=${encodeURIComponent(rel)}`,
    });

    cleanup.push(() => chat.destroy());   // the attachment thumbnails' object URLs

    // The first message became instruction.md (composed into the system prompt), so no
    // transcript event carries it — seed the chat with it as the opening user bubble.
    if (detail.instruction?.trim()) {
      chat.add({ type: "user_injection", payload: { text: detail.instruction.trim() } });
    }

    let autoscroll = true;
    const scrollDown = () => { if (autoscroll) window.scrollTo(0, document.body.scrollHeight); };
    const setState = (s) => {
      curState = s;
      stateChip.textContent = s;
      stateChip.className = `chip ${s}`;
      waiting.replaceChildren();
      if (WORKING.has(s)) waiting.append(busy(s === "queued" ? "queued for a slot…" : "working…"));
      // the conversation phase diagram lights the reply-cycle node from the live run state
      // (a conversation loops — it has no single workflow phase to highlight)
      phaseGraph.setPhase(WORKING.has(s) ? "working" : "waiting for you");
      composer.setLive(!TERMINAL.has(s));
      if (TERMINAL.has(s)) { chat.finishOpenFold(); artifacts.refresh(); subRunTree?.refresh();
                             fileActivity?.refresh(); scripts.refresh(); }
      bgTasks.refresh();     // a finished detached task wakes the conversation → catch it here
      branchBar?.refresh();  // D306 pt 2: a branch's state changed → its rail row with it
    };
    setState(detail.state);

    if (!detail.run_id) return;   // created but never fired (shouldn't happen)
    tail = liveTail({
      page: (o) => `/api/runs/${detail.run_id}/transcript?offset=${o}`,
      events: (o) => `/api/runs/${detail.run_id}/events?offset=${o}`,
      offset: 0,
      onEvent: (ev) => { if (pendingEcho && ev.type === "user_injection"
                             // typedBody both sides: the injected text arrives DRESSED (ref line +
                             // appended attachment block), the echo holds the raw typed text — a
                             // raw comparison never matched an attachment send (R473)
                             && typedBody(ev.payload?.text) === typedBody(pendingEcho.text)) {
                           pendingEcho.node?.remove(); pendingEcho = null;   // the real bubble takes over (F295)
                         }
                         chat.add(ev); scrollDown();
                         if (ev.type === "finish") pbFinal.refresh(ev.payload?.final === true);
                         if (ev.type === "child_start" || ev.type === "child_end") subRunTree?.poke();
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
      const input = el("textarea", { rows: 2, placeholder: "message…  (Ctrl+Enter sends · / for commands)" });
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
      // The playbook controls + D175's offer (components/playbook-row.js): save-as-playbook,
      // the server's "this did real work" prompt when it applies, and update-playbook when the
      // conversation was itself seeded from one.
      const pbRow = playbookRow(slug, detail);
      // Manual stop for a live reply — the run has no automatic backstop when its budgets
      // are set to -1 (unlimited), so the user must be able to end it at any time. Aborts at
      // the next turn boundary (the reply finishes as `aborted`, transcript + LEDGER intact).
      const stopBtn = el("button", { class: "btn small danger",
        title: "stop this reply now — it aborts at the next turn boundary" }, "✕ stop");
      stopBtn.hidden = true;
      stopBtn.onclick = async () => {
        stopBtn.disabled = true;
        try { await api(`/api/runs/${detail.run_id}/abort`, { method: "POST" }); toast("stopping the reply…"); }
        catch (err) { toastError(err); stopBtn.disabled = false; }
      };
      // D63: the Admin toggle — armed here, it lifts capability gating for the legs this
      // composer starts (components/admintoggle.js owns the session token and the header).
      const admin = adminToggle({
        title: "run this conversation with the full toolset — sends the admin token with each message",
        prompt: "Admin token — lifts capability gating for this conversation. Stored for this browser "
          + "session only; the server re-checks it on every message.",
        onMsg: "admin on — this conversation runs with the full toolset",
        offMsg: "admin off — messages send normally",
      });
      // D308 pt 3: delegate what is in the box as a DETACHED task instead of asking for a
      // reply about it. No reply is started — see components/bg-start.js.
      const bgBtn = backgroundButton({ slug,
        text: () => input.value,
        clear: () => { input.value = ""; forgetField(input); },
        onStarted: () => bgTasks.refresh() });
      const node = el("div", { class: "conv-composer" }, helpPanel, refBar,
        el("div", { class: "cmd-anchor" }, suggests, input),
        el("div", { class: "row", style: "gap:8px" },
           picker, helpBtn, admin.node, bgBtn, send, stopBtn), pbRow);
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
          const r = await apiUpload(`/api/conversations/${slug}/message`, fd, admin.headers());
          input.value = "";
          setRef(null);
          forgetField(input);   // sent — the draft must not refill on reload
          clearFiles();
          toast(r.command ? "command running — you keep the turn"
            : r.delivery === "mid-run" ? "delivered — picked up next turn"
            : "waking the conversation…");
          // F295: show the message AT ONCE — no transcript event carries it until the
          // woken leg (or the next turn) picks it up, and the user must see it landed
          if (!r.command) {
            const sentText = String(fd.get("text") || "");
            pendingEcho?.node?.remove();
            pendingEcho = { text: sentText, node: null, id: r.id || "" };
            pendingEcho.node = echoNode();
            echoBox.append(pendingEcho.node);
          }
          // Re-attach to show the result (command) or the live reply; a mid-run send is
          // already streaming. A conversation is ONE run resumed IN PLACE, so the head, the
          // rails and this composer are unchanged by a message — only the tail had ended.
          // Resuming it from its last offset costs one catch-up GET where remounting cost the
          // detail read, the full library lint behind the head's rule picker, and ~10 more rail
          // fetches PER MESSAGE, plus a re-render of the whole thread from offset 0.
          if (r.delivery === "mid-run") { /* the live tail carries it */ }
          else if (tail && r.run_id === detail.run_id) setTimeout(() => tail.resume(), 700);
          else setTimeout(mountConversation, 700);   // a fresh fire: a different run to follow
        } catch (err) { toastError(err, 5000); }
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
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); }
      };
      return { node, setRef,
               setLive: (live) => { stopBtn.hidden = !live; if (live) stopBtn.disabled = false; } };
    }
  }


}
