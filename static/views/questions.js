// Decisions inbox: everything open across routines — blocking questions, deferred ones,
// standing proposals, and the self-audit report's open decisions (meta badge) — in ONE
// answering surface, grouped by CLASS and priority (blocking > closing > meta > proposals > settled;
// asks about to hit their timeout are flagged "expiring" and sort first). Keyboard-first: the
// first pending item autofocuses, Enter submits, ↑/↓ move, 1–9 pick an option. Toolbar: filter
// by kind or routine, sort by priority/age/routine (non-priority sorts render flat).
//
// The categories themselves — the groups, the filter chips, the ordering and what counts as
// urgent — live in views/questions-taxonomy.js. PROPOSALS (R2425) are the group nothing is
// waiting on, so they render below the ones that are and carry NO urgency affordance: no
// expiring chip, no deadline sort, and an absolute `asked` stamp with no relative age, because
// a proposal that has sat three weeks is not late.

import { replaceHash } from "/static/router.js";
import { api } from "/static/api.js";
import { loadQuestions, subscribeQuestions, wantSettled } from "/static/questions-store.js";
import { SETTLED_PAGE, settledMore } from "/static/components/settled-more.js";
import { pendingBand } from "/static/components/pending.js";
import { answerForm } from "/static/components/answerform.js";
import { settledCard } from "/static/components/settled-card.js";
import { a2uiFold } from "/static/components/a2ui-decision.js";
import { linkifyRefs } from "/static/components/reflinks.js";
import { configProposal } from "/static/components/configproposal.js";
import { answeredChips, lifecycleControls, proofNote }
  from "/static/components/decision-lifecycle.js";
import { md, summaryLine } from "/static/md.js";
import { act, chip, el, emptyState, fmtAbs, groupHead, skeleton, when } from "/static/util.js";
import { TERMINAL } from "/static/states.js";
import { FILTERS, GROUPS, QTYPE_CHIPS, SORTS, disposed, expiringSoon, isBlocking, kindOf,
  sortQuestions, waitsOnYou } from "/static/views/questions-taxonomy.js";

// F621: a decision born as a REPORT — a routine routed work to another routine and the call
// is the operator's. The chip names the report and who acts once he answers, because that is
// what makes the card answerable without opening anything: the question text alone does not
// say that something is already queued on it.
const reportChip = (q) => (q.type === "report"
  ? chip(`report ${q.report || ""}${q.report_target ? ` → ${q.report_target}` : " → triage"}`,
         "partial")
  : null);
// Question text is MARKDOWN, block-rendered whoever wrote it: a run lays out what it found
// (a GFM table, a numbered list, a fenced snippet) before it asks, and inline those became
// literal pipes in the one place the user must read carefully. Finding/decision ids (F63, D14)
// link only in the audit's own voice (meta items); elsewhere a bare "D1" is a false positive.
const qBody = (q) => md(q.question);
const qText = (q) => {
  const node = el("div", { class: "q-text" }, qBody(q));
  return q.meta ? linkifyRefs(node) : node;
};
// each decision links its OWN home: a conversation's page lives under #/conversations,
// a detached task's surface is its owning conversation (the task itself has no page)
const sourceLink = (q) => (q.conversation ? el("a", { href: `#/conversations/${q.routine}` }, q.routine)
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
                  sort: "priority", items: [], snap: null };
  const take = (snapshot) => { state.snap = snapshot; state.items = snapshot.items; };
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
  // A decided proposal leaves the badge at once rather than at the next bus event.
  const pending = pendingBand({ onChanged: () => loadQuestions().catch(() => {}) });
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
    return sortQuestions(qs, state.sort);
  }

  function syncToolbar() {
    // The counts are of what WAITS, so an answered decision is not one of them. They counted
    // every unsnoozed row, answered included, so a fleet with twelve settled proposals and
    // nothing open read "All · 12 · Deferred · 12" — the page's own question ("does anything
    // need me?") answered wrongly, and the emptyState below could never fire.
    const open = state.items.filter((q) => !q.snoozed && !disposed(q));
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
            "The routines are self-sufficient. Blocking decisions hold a run's work up until you answer; closing and meta ones shape the next run; proposals wait for you, with no clock on them."));
      return;
    }
    // Nothing open, but settled rows to show: say so at the top rather than leaving the reader
    // to scroll a page of answered proposals looking for one that is not.
    if (state.filter !== "snoozed" && !qs.some((q) => !disposed(q)))
      list.append(emptyState("✓", "Nothing waits on you",
        "Every decision here is answered. Blocking decisions hold a run's work up until you answer; closing and meta ones shape the next run; proposals wait for you, with no clock on them."));
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
    const more = state.filter !== "snoozed"
      && settledMore(state.snap, (snap) => { take(snap); renderList(); });
    if (more) list.append(more);
    if (focus) focusAt(0);
  }

  async function load({ focus = true } = {}) {
    try { take(await loadQuestions()); }
    catch (err) { list.replaceChildren(emptyState("✕", "Couldn't load decisions", err.message)); return; }
    renderList({ focus });
  }

  // The config bridge — the factory both card shapes call — lives in
  // components/configproposal.js: three PATCH surfaces (a routine, a conversation, the
  // INSTANCE) and the honesty gate that verifies every key actually landed.
  const proposal = (q, opts) => configProposal(q, {
    ...opts,
    onApplied: () => {
      state.items = state.items.filter((x) => x.qid !== q.qid);
      syncToolbar();
    },
  });

  function item(q, index) {
    // Already answered (the inbox file exists; the routine consumes it on its next turn/run):
    // show the settled state instead of re-asking — reloads must not resurrect it as open.
    if (disposed(q)) {
      // The RECEIPT is its own component (components/settled-card.js): what you said and what
      // became of it, the revise affordance, and a config proposal answering did not apply. The
      // meta row and the question's rendering are built HERE, because the open card below builds
      // the same row and a second construction of it is a second place for the chips to drift.
      return settledCard(q, {
        proposal,
        meta: el("div", { class: "q-meta" },
          q.meta ? chip("meta", "meta") : null,
          QTYPE_CHIPS[q.type] ? chip(...QTYPE_CHIPS[q.type]) : null,
          reportChip(q),
          // What the answer WAS and what became of it — a decline in place of the answered chip
          // (R2197), a proof verdict beside it (D220); composed in decision-lifecycle.js.
          ...answeredChips(q),
          sourceLink(q),
          q.asked ? el("span", {}, "asked ", when(q.asked)) : null,
          q.consumed ? el("span", { class: "faint small" }, "read ", when(q.consumed)) : null),
        question: el("details", { class: "q-settled" },
          el("summary", { class: "q-text prose" },
            summaryLine(q.question, "(no question)")),
          qText(q)),
      });
    }
    const runBits = q.run_id ? [
      el("a", { class: "btn small", href: `#/run/${q.run_id}` }, "view run"),
      q.run_state ? chip(q.run_state, q.run_state) : null,
      q.run_state && TERMINAL.has(q.run_state)
        ? el("span", { class: "faint small" }, "run already ended — the answer feeds the next one") : null,
    ] : [];
    // Lifecycle controls beside the answer — DECLINE (the first-class no, R2197), defer-to-
    // next-run and snooze: the three ways a card leaves this page without an answer in prose.
    // Their own component (components/decision-lifecycle.js): none is the answer protocol the
    // form owns. `panel`/`controls`/`form` are declared below and reached at CLICK time.
    const lifecycle = lifecycleControls(q, {
      settle: (label, note, cls = "partial") => {
        panel.classList.remove("warn");
        controls.replaceChildren(el("div", { class: "flow-note" },
          chip(label, cls), el("span", {}, note)));
      },
      refresh: () => renderList(),
      syncToolbar,
      reason: () => form.input.value,
    });
    // "answer & run now": the operator's deliberate fire beside the answer — a manual run,
    // the same as the routine page's Run now. Only for a deferred routine question whose
    // run has ended: a blocking one resumes its own run, a meta one is self-audit's, and a
    // live run drains the answer at its next turn boundary. Nothing fires on its own —
    // an answer WAITS for the next scheduled run unless this button is the one clicked.
    let wantRun = false;
    let firedRunId = null;
    const canRunNow = !q.meta && q.mode !== "blocking" && !q.conversation && !q.background
      && (!q.run_state || TERMINAL.has(q.run_state));
    const runNow = canRunNow ? el("button", { class: "btn small", "data-answer-run-now": "",
      title: "file this answer AND start one run of the routine now (a manual run) — "
        + "otherwise the answer waits for its next scheduled run" }, "answer & run now") : null;
    // the run started by hand may take a one-line BRIEF, which it answers for instead of its
    // recipe's Done when — the same optional line the routine page's Run now offers
    const briefIn = canRunNow ? el("input", { type: "text", class: "run-brief tight",
      maxlength: "300", placeholder: "brief for that run (optional)", "data-answer-brief": "",
      "data-nopersist": true,
      title: "one job for the run \"answer & run now\" starts — leave it empty for an ordinary run" })
      : null;
    const form = answerForm(q, {
      control: "input",
      placeholder: "your answer…  (↵ to send)",
      numbered: true,
      defaultLine: false,          // the panel body renders the default line below
      onArrow: (d) => focusAt(index + d),
      submitText: async (text, _intermediate, decision) => {
        firedRunId = null;
        const brief = wantRun ? (briefIn?.value || "").trim() : "";
        const result = await api(`/api/questions/${q.qid}/answer`,
          { method: "POST", body: { ...(decision ? { decision } : { text }),
                                    ...(wantRun ? { run_now: true } : {}),
                                    ...(brief ? { brief } : {}) } });
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
        // R2189: a card carrying an UNAPPLIED `config_patch` STAYS on the page with its
        // proposal and its `approve & apply` button (operator's ruling, 2026-10-05, option 3:
        // "only the button; answering warns and keeps the patch available"). Answering never
        // applies a patch, so dropping the card here destroyed the only control that could —
        // reported twice, the second time on an option reading "Ja — Patch übernehmen".
        //
        // Keeping it listed is only half: the next repaint renders an answered card through
        // the SETTLED-RECEIPT branch at the top of `item`, a different element tree, so the
        // proposal has to be mounted THERE (it is — `settledConfig`). Marking `q.answered`
        // here is what routes it to that branch, and it keeps the card out of the open groups
        // while it waits for the button.
        if (q.config_patch && !q.meta && !q.config_patch_applied) {
          q.answered = true;
          q.answer = text;
          controls.append(el("div", { class: "small faint mt" },
            "the proposed config change was NOT applied — answering never applies it; "
            + "use “approve & apply” above while it is still offered"));
        } else {
          state.items = state.items.filter((x) => x.qid !== q.qid);
        }
        syncToolbar();
        // A repaint while the answer was in flight rebuilt `inputs` without this box — and
        // splicing at index -1 would drop the LAST question's box from the ↑/↓ order instead.
        const at = inputs.indexOf(form.input);
        if (at >= 0) inputs.splice(at, 1);
        focusAt(index);          // move on to the next open question
      },
      extraControls: [briefIn, runNow, ...lifecycle].filter(Boolean),
    });
    if (runNow) runNow.onclick = () => { wantRun = true; form.submit(false); };
    inputs.push(form.input);
    const controls = el("div", {}, form.node);
    // The config proposal (its factory is above) mounts its own copy here. It is built before
    // `panel` exists, so `clearWarn` reaches the panel through the slot rather than closing
    // over a name still in its temporal dead zone.
    const configSlot = el("div", {});
    const configBar = proposal(q, {
      host: controls, clearWarn: () => configSlot.closest(".question-item")?.classList.remove("warn"),
    });
    if (configBar) configSlot.append(configBar);
    const panel = el("div", { class: `panel question-item${isBlocking(q) ? " warn" : ""}` },
      el("div", { class: "q-meta" },
        expiringSoon(q) ? chip("expiring", "failed") : null,
        q.meta ? chip("meta", "meta") : null,
        QTYPE_CHIPS[q.type] ? chip(...QTYPE_CHIPS[q.type]) : null,
        reportChip(q),
        chip(q.mode, q.mode),
        q.snoozed ? chip("snoozed", "meta") : null,
        sourceLink(q),
        // The age affordance, and the one place a PROPOSAL differs from an ask (R2425):
        // `when()` renders a self-refreshing relative age ("3w ago") beside the stamp, which
        // on a standing proposal reads as lateness — nothing is waiting, so there is nothing
        // to be late for. A proposal shows the date it was filed, flat.
        q.asked
          ? (waitsOnYou(q) ? el("span", {}, "asked ", when(q.asked))
              : el("span", { class: "faint small",
                             title: "filed by a run that has ended — nothing consumes this "
                                    + "answer until you decide, or until the routine runs again" },
                  "filed ", el("span", { class: "when" }, fmtAbs(q.asked))))
          : null,
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
      configSlot,
      controls,
      // D310 step 5: the same decision RENDERED THROUGH A2UI, one click away under the card it
      // stands beside. Not a replacement, deliberately — the operator answers real work on this
      // page, and swapping the control he relies on for a protocol rendering he has not seen
      // would be the wrong order. It is a `<details>` so the surface is MOUNTED on first open
      // rather than on every repaint: a page of decisions must not fetch an envelope per card.
      // Answered through the protocol is answered: the card leaves the open list exactly as it
      // does when the form posts. MEASURED red-first: the shared questions store's own bus
      // snapshot already re-renders it as the settled receipt, so this callback is NOT what
      // makes that true — it only removes the wait for that round trip, which is why neutering
      // it leaves the browser test green. Kept for the latency, not the outcome.
      a2uiFold(q.qid, { onAnswered: () => {
        state.items = state.items.filter((x) => x.qid !== q.qid);
        syncToolbar();
        renderList({ focus: false });
      } }));
    return panel;
  }

  wantSettled(SETTLED_PAGE);   // the one reader that shows history asks for it (F810)
  await load();
  // A READER of the shared questions store (questions-store.js), which owns the bus listener
  // and coalesces every surface's refresh into one fetch — this view used to fetch on EVERY
  // bus event, several GETs a second while a run worked, each rebuilding every card.
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
    take(snapshot);
    renderList({ focus: false });
  };
  const unsubscribe = subscribeQuestions(paint);
  list.addEventListener("focusout", () => {
    // focusout fires before focus settles on the next node — re-check on the next tick
    setTimeout(() => { if (deferred && !typingHere()) paint(deferred); }, 0);
  });
  return () => { unsubscribe(); wantSettled(0); };
}
