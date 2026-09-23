// Decisions inbox: everything open across routines — blocking questions, deferred ones,
// and the self-audit report's open decisions (meta badge) — in ONE answering surface,
// grouped by priority (blocking > deferred > meta > settled; asks about to hit their
// timeout are flagged "expiring" and sort first). Keyboard-first: the first pending item
// autofocuses, Enter submits, ↑/↓ move, 1–9 pick an option. Toolbar: filter by kind or
// routine, sort by priority/age/routine (non-priority sorts render flat).

import { replaceHash } from "/static/router.js";
import { api } from "/static/api.js";
import { loadQuestions, subscribeQuestions } from "/static/questions-store.js";
import { pendingBand } from "/static/components/pending.js";
import { answerForm } from "/static/components/answerform.js";
import { linkifyRefs } from "/static/components/reflinks.js";
import { md, summaryLine } from "/static/md.js";
import { act, chip, el, emptyState, groupHead, skeleton, toast, toastError, when } from "/static/util.js";
import { TERMINAL } from "/static/states.js";

const FILTERS = [["all", "All"], ["blocking", "Blocking"], ["deferred", "Deferred"], ["meta", "Meta"], ["snoozed", "Snoozed"]];
const SNOOZES = [["60", "1 hour"], ["240", "4 hours"], ["1440", "1 day"], ["10080", "1 week"]];
const SORTS = [["priority", "priority"], ["newest", "newest"], ["oldest", "oldest"], ["routine", "routine"]];

const rank = (q) => (q.answered ? 3 : q.mode === "blocking" ? 0 : q.meta ? 2 : 1);
// inbox groups, strongest first — the priority sort renders these as sections
// [noun, what the group is, membership, does it wait on a person]
const GROUPS = [
  ["Blocking", "a run is waiting on you", (q) => !q.answered && q.mode === "blocking", true],
  ["Deferred", "the next run picks these up",
   (q) => !q.answered && !q.meta && q.mode !== "blocking", true],
  ["Meta", "system-level decisions", (q) => !q.answered && q.meta, true],
  ["Settled", "what you answered, and what became of it", (q) => q.answered, false],
];
const EXPIRING_MS = 30 * 60 * 1000;   // a blocking ask this close to its timeout is LOUD
const expiringSoon = (q) => q.mode === "blocking" && q.expires
  && new Date(q.expires).getTime() - Date.now() < EXPIRING_MS;
const kindOf = (q) => (q.meta ? "meta" : q.mode);
// audit decisions reference findings/decisions by id (F63, D14) — make those clickable,
// but ONLY in the audit's own voice (meta items); elsewhere a bare "D1" is a false positive
// Render the question text as MARKDOWN, with the BLOCK renderer, whoever wrote it. A run lays
// out what it found before it asks — the ask-policy rule and the deliberation contract both
// push it to — so an ordinary deferred question arrives carrying a GFM table of counts, a
// numbered list of options and a fenced snippet, exactly as a meta decision does. Rendered
// inline those become literal pipes and asterisks in the one place the user has to read
// carefully enough to decide. md() is a superset of mdInline(), so nothing reads worse for it.
const qBody = (q) => md(q.question);
const qText = (q) => {
  const node = el("div", { class: "q-text" }, qBody(q));
  return q.meta ? linkifyRefs(node) : node;
};
const sourceLink = (q) => (q.wizard
  // a clarify session's surface is its run page (D11); a pre-D13 session has none
  ? (q.run_id ? el("a", { href: `#/run/${q.run_id}` }, "new-routine setup")
              : el("span", { class: "muted" }, "new-routine setup"))
  // each decision links its OWN home: a conversation's page lives under #/conversations,
  // a detached task's surface is its owning conversation (the task itself has no page)
  : q.conversation ? el("a", { href: `#/conversations/${q.routine}` }, q.routine)
  : q.background ? (q.owner
      ? el("a", { href: `#/conversations/${q.owner}`, title: "the conversation that launched this background task" },
          `${q.routine} (background)`)
      : el("span", { class: "muted" }, `${q.routine} (background)`))
  : el("a", { href: `#/routine/${q.routine}` }, q.routine));

export async function render(view, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("h1", {}, "Decisions"),
      el("div", { class: "sub" }, "answers the routines need from you — one inbox, blocking first")),
    el("div", { class: "kbd-hint" },
      el("kbd", {}, "↵"), " answer · ", el("kbd", {}, "↑"), el("kbd", {}, "↓"), " move · ",
      el("kbd", {}, "1"), "–", el("kbd", {}, "9"), " pick option")));

  // filter + routine live in the URL (#/questions?filter=…&routine=…) so a routine
  // page's "answer" link can deep-link straight to its own open decisions
  const state = { filter: query.filter || "all", routine: query.routine || "",
                  sort: "priority", items: [] };
  const syncURL = () => replaceHash("#/questions", {
    ...(state.filter !== "all" ? { filter: state.filter } : {}),
    ...(state.routine ? { routine: state.routine } : {}) });

  const filterChips = new Map();
  const chipRow = el("div", { class: "row", style: "gap:6px" });
  for (const [key, label] of FILTERS) {
    const b = el("button", { class: "btn small" }, label);
    b.onclick = () => { state.filter = key; syncURL(); renderList(); };
    filterChips.set(key, b);
    chipRow.append(b);
  }
  const routineSel = el("select", { class: "small" });
  routineSel.onchange = () => { state.routine = routineSel.value; syncURL(); renderList(); };
  const sortSel = el("select", { class: "small" });
  for (const [key, label] of SORTS) sortSel.append(el("option", { value: key }, `sort: ${label}`));
  sortSel.onchange = () => { state.sort = sortSel.value; renderList(); };
  view.append(el("div", { class: "row mt toolbar", style: "gap:10px" }, chipRow, routineSel, sortSel));

  // F328: creations a SCHEDULED run proposed — it had no user in the loop, so it queued
  // instead of creating. Above the decisions because nothing happens until one is clicked.
  const pending = pendingBand();
  view.append(pending.node);

  const list = el("div", { class: "mt" });
  list.append(skeleton(), skeleton());
  view.append(list);

  const inputs = [];   // answer inputs, in render order, for ↑/↓ focus moves

  function focusAt(i) {
    const input = inputs[Math.max(0, Math.min(inputs.length - 1, i))];
    if (input) { input.focus(); input.scrollIntoView({ block: "center", behavior: "smooth" }); }
  }

  function visible() {
    let qs = state.items;
    // snoozed items live in their own bucket — hidden from every other view of the inbox
    if (state.filter === "snoozed") qs = qs.filter((q) => q.snoozed);
    else {
      qs = qs.filter((q) => !q.snoozed);
      if (state.filter !== "all") qs = qs.filter((q) => kindOf(q) === state.filter);
    }
    if (state.routine) qs = qs.filter((q) => q.routine === state.routine);
    const byAsked = (a, b) => String(a.asked || "").localeCompare(String(b.asked || ""));
    if (state.sort === "priority") {
      qs = [...qs].sort((a, b) => rank(a) - rank(b)
        || String(a.expires || "9999").localeCompare(String(b.expires || "9999"))
        || byAsked(a, b));
    }
    else if (state.sort === "oldest") qs = [...qs].sort(byAsked);
    else if (state.sort === "newest") qs = [...qs].sort((a, b) => byAsked(b, a));
    else if (state.sort === "routine") {
      qs = [...qs].sort((a, b) =>
        a.routine.localeCompare(b.routine) || rank(a) - rank(b) || byAsked(a, b));
    }
    return qs;
  }

  function syncToolbar() {
    // The counts are of what WAITS, so an answered decision is not one of them. They counted
    // every unsnoozed row, answered included, so a fleet with twelve settled proposals and
    // nothing open read "All · 12 · Deferred · 12" — the page's own question ("does anything
    // need me?") answered wrongly, and the emptyState below could never fire.
    const open = state.items.filter((q) => !q.snoozed && !q.answered);
    const counts = { all: open.length,
                     snoozed: state.items.filter((q) => q.snoozed).length };
    for (const q of open) counts[kindOf(q)] = (counts[kindOf(q)] || 0) + 1;
    for (const [key, b] of filterChips) {
      const n = counts[key] || 0;
      b.textContent = `${FILTERS.find(([k]) => k === key)[1]} · ${n}`;
      b.classList.toggle("primary", state.filter === key);
      b.disabled = key !== "all" && n === 0;
    }
    const routines = [...new Set(state.items.map((q) => q.routine))].sort();
    routineSel.replaceChildren(el("option", { value: "" }, "all routines"),
      ...routines.map((r) => el("option", { value: r }, r)));
    routineSel.value = routines.includes(state.routine) ? state.routine : "";
  }

  function renderList({ focus = false } = {}) {
    syncToolbar();
    inputs.length = 0;
    list.replaceChildren();
    const qs = visible();
    if (!qs.length) {
      list.append(state.items.length
        ? emptyState("◌", "Nothing matches this filter", "Widen the filter above — there are open items elsewhere.")
        : emptyState("✓", "No decisions to make right now",
            "The routines are self-sufficient. Blocking questions pause their run here; deferred and meta ones wait for the next run."));
      return;
    }
    // Nothing open, but settled rows to show: say so at the top rather than leaving the reader
    // to scroll a page of answered proposals looking for one that is not.
    if (state.filter !== "snoozed" && !qs.some((q) => !q.answered))
      list.append(emptyState("✓", "Nothing waits on you",
        "Every decision here is answered. Blocking questions pause their run here; deferred and meta ones wait for the next run."));
    if (state.sort === "priority") {
      let i = 0;
      for (const [noun, explain, match, waits] of GROUPS) {
        const members = qs.filter(match);
        if (!members.length) continue;
        list.append(groupHead(noun, members.length, explain, { waits }));
        for (const q of members) list.append(item(q, i++));
      }
    } else {
      qs.forEach((q, i) => list.append(item(q, i)));
    }
    if (focus) focusAt(0);
  }

  async function load({ focus = true } = {}) {
    try { state.items = (await loadQuestions()).items; }
    catch (err) { list.replaceChildren(emptyState("✕", "Couldn't load decisions", err.message)); return; }
    renderList({ focus });
  }

  function item(q, index) {
    // Already answered (the inbox file exists; the routine consumes it on its next turn/run):
    // show the settled state instead of re-asking — reloads must not resurrect it as open.
    if (q.answered) {
      // What you said stays on the page (F525). Before the durable record, consuming an
      // answer deleted it: you answered, a run booted, and your own words were gone from
      // the only surface that had shown them. A settled decision now keeps its card — and
      // stays revisable, because answering one of four questions and wanting to add the
      // other three is an ordinary thing to do, not an error to be locked out of.
      const body = el("div", {});
      const say = (text, note) => body.replaceChildren(
        el("div", { class: "flow-note" }, el("span", {}, `“${text}” ${note}`)),
        el("div", { class: "row mt" }, reviseBtn));
      const editor = () => {
        const box = el("textarea", { class: "answer-input", rows: "3",
          "data-persist": `revise-${q.qid}` });
        box.value = q.answer || "";
        const save = el("button", { class: "btn small primary" }, "save revision");
        const cancel = el("button", { class: "btn small" }, "cancel");
        cancel.onclick = () => say(q.answer, settledNote());
        save.onclick = () => {
          const text = box.value.trim();
          if (!text) return;
          // act() re-enables in a `finally`, which is the only place it cannot be forgotten:
          // this handler used to re-enable only on the failure path, so a revision that SAVED
          // left its own button dead.
          act(save, async () => {
            const r = await api(`/api/questions/${q.qid}/revise`, { method: "POST", body: { text } });
            q.answer = text;
            q.settled = false;              // re-queued: a run has to read the amendment
            say(text, "→ inbox → the next run reads this instead");
            return r;
          }, "revised — the next run reads the new answer");
        };
        body.replaceChildren(box, el("div", { class: "row mt" }, save, cancel));
        box.focus();
      };
      const reviseBtn = el("button", { class: "btn small",
        title: "change what you answered — a decision a run already read is re-queued so the next one sees the amendment" },
        "revise");
      reviseBtn.onclick = editor;
      const settledNote = () => (q.settled
        ? "→ read by the run that asked"
        : `→ inbox → consumed by the ${q.mode === "blocking" ? "waiting run"
            : q.ran_now ? "run starting now" : "next run"}`);
      say(q.answer, settledNote());
      return el("div", { class: "panel question-item answered" },
        el("div", { class: "q-meta" },
          q.wizard ? chip("clarify", "meta") : q.meta ? chip("meta", "meta") : null,
          q.type === "util-approval" ? chip("util approval", "partial") : null,
          q.type === "request" ? chip("access request", "partial") : null,
          chip(`answered${q.answer_source && q.answer_source !== "web" ? ` via ${q.answer_source}` : ""}`
            + ` · ${q.settled ? "acted on" : q.ran_now ? "run started" : "queued"}`, "ok"),
          sourceLink(q),
          q.asked ? el("span", {}, "asked ", when(q.asked)) : null,
          q.consumed ? el("span", { class: "faint small" }, "read ", when(q.consumed)) : null),
        // A settled card is a RECEIPT: what was asked, what you said, and the chance to revise.
        // Rendering the whole proposal body — rationale, prior art, sketch, an impact table —
        // above the answer made the page eleven thousand pixels of decisions already taken. The
        // first line leads; the rest is one click, and nothing is dropped.
        el("details", { class: "q-settled" },
          el("summary", { class: "q-text prose" },
            summaryLine(q.question, "(no question)")),
          qText(q)),
        el("div", { class: "mt" }, body));
    }
    const runBits = q.run_id ? [
      el("a", { class: "btn small", href: `#/run/${q.run_id}` }, "view run"),
      q.run_state ? chip(q.run_state, q.run_state) : null,
      q.run_state && TERMINAL.has(q.run_state)
        ? el("span", { class: "faint small" }, "run already ended — the answer feeds the next one") : null,
    ] : [];
    // Lifecycle controls beside the answer: a BLOCKING question can be deferred to the
    // next run (unblocks the run on its stated default, record stays open); every other
    // file-backed record can be snoozed (hidden here until a timestamp — runs still see it).
    let lifecycle = null;
    if (q.mode === "blocking" && !q.meta) {
      lifecycle = el("button", { class: "btn small",
        title: "unblock the run WITHOUT deciding — it continues on its stated default and the question stays open for the next run" },
        "defer to next run");
      lifecycle.onclick = async () => {
        lifecycle.disabled = true;
        try {
          await api(`/api/questions/${q.qid}/defer`, { method: "POST", body: {} });
          toast("deferred — the run continues on its default");
          panel.classList.remove("warn");
          controls.replaceChildren(el("div", { class: "flow-note" },
            chip("deferred to next run", "partial"),
            el("span", {}, "the run continues on its default — the question stays open")));
          q.mode = "deferred";
          syncToolbar();
        } catch (err) { toastError(err); lifecycle.disabled = false; }
      };
    } else if (!q.meta) {
      if (q.snoozed) {
        lifecycle = el("button", { class: "btn small", title: "bring it back into the inbox now" },
          "unsnooze");
        lifecycle.onclick = async () => {
          try {
            await api(`/api/questions/${q.qid}/snooze`, { method: "POST", body: { minutes: 0 } });
            toast("back in the inbox");
            q.snoozed = false;
            delete q.snoozed_until;
            renderList();
          } catch (err) { toastError(err); }
        };
      } else {
        lifecycle = el("select", { class: "small", "data-nopersist": true,
          title: "hide this decision here for a while — the routine still sees it as open" },
          el("option", { value: "" }, "snooze…"),
          ...SNOOZES.map(([min, label]) => el("option", { value: min }, label)));
        lifecycle.onchange = async () => {
          if (!lifecycle.value) return;
          try {
            const r = await api(`/api/questions/${q.qid}/snooze`,
              { method: "POST", body: { minutes: +lifecycle.value } });
            toast("snoozed — it waits under the Snoozed filter");
            q.snoozed = true;
            q.snoozed_until = r.snoozed_until;
            renderList();
          } catch (err) { toastError(err); lifecycle.value = ""; }
        };
      }
    }
    // "answer & run now": the operator's deliberate fire beside the answer — a manual run,
    // the same as the routine page's Run now. Only for a deferred routine question whose
    // run has ended: a blocking one resumes its own run, a meta one is self-audit's, and a
    // live run drains the answer at its next turn boundary. Nothing fires on its own —
    // an answer WAITS for the next scheduled run unless this button is the one clicked.
    let wantRun = false;
    let firedRunId = null;
    const canRunNow = !q.meta && q.mode !== "blocking" && !q.conversation && !q.background
      && !q.wizard && (!q.run_state || TERMINAL.has(q.run_state));
    const runNow = canRunNow ? el("button", { class: "btn small", "data-answer-run-now": "",
      title: "file this answer AND start one run of the routine now (a manual run) — "
        + "otherwise the answer waits for its next scheduled run" }, "answer & run now") : null;
    const form = answerForm(q, {
      control: "input",
      placeholder: "your answer…  (↵ to send)",
      numbered: true,
      defaultLine: false,          // the panel body renders the default line below
      onArrow: (d) => focusAt(index + d),
      submitText: async (text, _intermediate, decision) => {
        firedRunId = null;
        const result = await api(`/api/questions/${q.qid}/answer`,
          { method: "POST", body: { ...(decision ? { decision } : { text }),
                                    ...(wantRun ? { run_now: true } : {}) } });
        firedRunId = result.run_id || null;
        return result;
      },
      toastText: () => (q.mode === "blocking" ? "answered — the run resumes"
        : q.meta ? "recorded — the next self-audit run acts on it"
        : firedRunId ? "answered — a run is starting now"
        : "answered — the next run picks it up"),
      // Mark answered in place: a deferred question's pending file is only consumed when
      // its routine next runs, so a reload would still list it — that would read as
      // "didn't work".
      onSuccess: (text) => {
        panel.classList.remove("warn");
        controls.replaceChildren(el("div", { class: "flow-note" },
          chip(firedRunId ? "answered · run started" : "answered · queued", "ok"),
          el("span", {}, `“${text}” → inbox → consumed by the ${q.mode === "blocking" ? "waiting run"
            : firedRunId ? "run starting now" : "next run"}`)));
        state.items = state.items.filter((x) => x.qid !== q.qid);
        syncToolbar();
        inputs.splice(inputs.indexOf(form.input), 1);
        focusAt(index);          // move on to the next open question
      },
      extraControls: runNow && lifecycle ? [runNow, lifecycle] : (runNow || lifecycle),
    });
    if (runNow) runNow.onclick = () => { wantRun = true; form.submit(false); };
    inputs.push(form.input);
    const controls = el("div", {}, form.node);
    // Config bridge: a revise run can't edit routine.yaml, so it proposes the change as a
    // config_patch on the decision; approving it here PATCHes the owning config and resolves
    // the ask. Each home has its own PATCH surface (R102: a conversation's decision must hit
    // /api/conversations — PATCHing /api/routines with a conversation slug 404s, so the
    // patch silently never landed); a detached task / clarify workspace has none — its
    // proposal renders read-only rather than pretending a button would work.
    const configBar = (q.config_patch && !q.meta) ? (() => {
      // R1488: a domain's shared block is config too, and `PATCH /api/domains/{id}` has always
      // existed — but with the home derived from the ASKER's kind alone, a domain could never
      // be the target, so every domain-level proposal came out as prose asking the operator to
      // go and click it. The engine now resolves target AND home together at ask time, so an
      // explicit `config_home` is authoritative here and the button posts where it says.
      const home = q.config_home ? q.config_home
        : q.conversation ? "conversations" : (q.background || q.wizard) ? "" : "routines";
      const noun = home === "domains" ? "domain" : q.conversation ? "conversation" : "routine";
      // D123/F458: a config_patch may be FOR another routine (config-optimizer's whole job).
      // The engine resolved and validated that slug at ask time (engine/interact.py), so the
      // patch goes to the TARGET, not to whoever asked — the old hardwiring to q.routine
      // silently rewrote the asker's own config and reported success (R1343).
      const target = (!q.conversation && q.config_target) ? q.config_target : q.routine;
      const elsewhere = home === "domains" || target !== q.routine;
      const btn = home ? el("button", { class: "btn small primary" }, "approve & apply") : null;
      if (btn) btn.onclick = async () => {
        btn.disabled = true;
        try {
          const res = await api(`/api/${home}/${target}`,
            { method: "PATCH", body: q.config_patch });
          // Honesty gate (R102): a field the endpoint doesn't support is silently dropped
          // server-side — verify every patch key was actually applied before telling the
          // routine (and the user) it was. `updated` is the endpoint's applied-field list.
          const missing = Object.keys(q.config_patch)
            .filter((k) => !(res.updated || []).includes(k));
          if (missing.length) {
            throw new Error(`${missing.join(", ")}: not applicable to a ${noun} — `
              + "the proposal needs a different route; answer in text instead");
          }
          await api(`/api/questions/${q.qid}/answer`,
            { method: "POST", body: { text: "approved & applied the proposed config change" } });
          toast(elsewhere ? `config change applied to ${target}`
                          : `config change applied to the ${noun}`);
          panel.classList.remove("warn");
          controls.replaceChildren(el("div", { class: "flow-note" },
            chip("applied", "ok"),
            el("span", {}, elsewhere ? `the config change was applied to ${target}`
                                     : `the config change was applied to the ${noun}`)));
          state.items = state.items.filter((x) => x.qid !== q.qid);
          syncToolbar();
        } catch (err) { toastError(err, 5000); btn.disabled = false; }
      };
      return el("div", { class: "flow-note mt" },
        el("div", { class: "small", style: "margin-bottom:4px" },
          home ? (elsewhere
                 ? `proposed config change for ${target} — ${q.routine} asked for it on that `
                   + `${noun}'s behalf; approving it patches `
                   + `${target}, not ${q.routine}:`
                 : "proposed config change — a run can't edit routine.yaml, so approve it here:")
               : "proposed config change — this decision's home has no config to patch "
                 + "(a detached task / setup workspace is one-shot); answer in text, and make "
                 + "any lasting change on the owning conversation or routine:"),
        el("pre", { class: "doc", style: "margin:0 0 6px;white-space:pre-wrap" },
          JSON.stringify(q.config_patch, null, 2)),
        btn);
    })() : null;
    const panel = el("div", { class: `panel question-item${q.mode === "blocking" ? " warn" : ""}` },
      el("div", { class: "q-meta" },
        expiringSoon(q) ? chip("expiring", "failed") : null,
        q.wizard ? chip("clarify", "meta") : q.meta ? chip("meta", "meta") : null,
        q.type === "util-approval" ? chip("util approval", "partial") : null,
        q.type === "request" ? chip("access request", "partial") : null,
        chip(q.mode, q.mode),
        q.snoozed ? chip("snoozed", "meta") : null,
        sourceLink(q),
        q.asked ? el("span", {}, "asked ", when(q.asked)) : null,
        q.snoozed && q.snoozed_until
          ? el("span", { class: "faint small" }, "returns ", when(q.snoozed_until, { mode: "rel" })) : null,
        q.mode === "blocking" && q.expires
          ? el("span", { class: "faint small", title: "when the run continues without an answer" },
              "continues without you ", when(q.expires, { mode: "rel" })) : null,
        ...runBits),
      qText(q),
      q.default ? el("div", { class: "faint small mt",
        title: "what the routine does if this stays unanswered" },
        `↪ without an answer: ${q.default}`) : null,
      configBar,
      controls);
    return panel;
  }

  await load();
  // This page is a READER of the shared questions store (questions-store.js): it owns the bus
  // listener, skips the llm_task/llm_process storm, and coalesces every surface's refresh into
  // one fetch. This view used to fetch on EVERY bus event with no filter and no timer —
  // several GETs a second while a run worked, each rebuilding every card.
  //
  // The rebuild (renderList → list.replaceChildren) also yanks focus out of the answer field
  // you are typing into: on mobile it dismisses the keyboard and drops the caret, so the answer
  // never lands. Defer the repaint while an answer control in this list holds focus, and flush
  // the deferred one once focus leaves.
  let deferred = null;
  const typingHere = () => {
    const a = document.activeElement;
    return Boolean(a && list.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName));
  };
  const paint = (snapshot) => {
    if (typingHere()) { deferred = snapshot; return; }
    deferred = null;
    state.items = snapshot.items;
    renderList({ focus: false });
  };
  const unsubscribe = subscribeQuestions(paint);
  list.addEventListener("focusout", () => {
    // focusout fires before focus settles on the next node — re-check on the next tick
    setTimeout(() => { if (deferred && !typingHere()) paint(deferred); }, 0);
  });
  return unsubscribe;
}
