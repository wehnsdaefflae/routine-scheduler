// Decisions inbox: everything open across routines — blocking questions, deferred ones,
// and the self-audit report's open decisions (meta badge) — in ONE answering surface,
// grouped by priority (blocking > deferred > meta > settled; asks about to hit their
// timeout are flagged "expiring" and sort first). Keyboard-first: the first pending item
// autofocuses, Enter submits, ↑/↓ move, 1–9 pick an option. Toolbar: filter by kind or
// routine, sort by priority/age/routine (non-priority sorts render flat).

import { replaceHash } from "/static/router.js";
import { api } from "/static/api.js";
import { answerForm } from "/static/components/answerform.js";
import { linkifyRefs } from "/static/components/reflinks.js";
import { md, mdInline } from "/static/md.js";
import { chip, el, emptyState, skeleton, toast, when } from "/static/util.js";
import { TERMINAL } from "/static/states.js";

const FILTERS = [["all", "All"], ["blocking", "Blocking"], ["deferred", "Deferred"], ["meta", "Meta"], ["snoozed", "Snoozed"]];
const SNOOZES = [["60", "1 hour"], ["240", "4 hours"], ["1440", "1 day"], ["10080", "1 week"]];
const SORTS = [["priority", "priority"], ["newest", "newest"], ["oldest", "oldest"], ["routine", "routine"]];

const rank = (q) => (q.answered ? 3 : q.mode === "blocking" ? 0 : q.meta ? 2 : 1);
// inbox groups, strongest first — the priority sort renders these as sections
const GROUPS = [
  ["Blocking — a run is waiting on you", (q) => !q.answered && q.mode === "blocking"],
  ["Deferred — the next run picks these up", (q) => !q.answered && !q.meta && q.mode !== "blocking"],
  ["Meta — system-level decisions", (q) => !q.answered && q.meta],
  ["Settled — answered, queued for pickup", (q) => q.answered],
];
const EXPIRING_MS = 30 * 60 * 1000;   // a blocking ask this close to its timeout is LOUD
const expiringSoon = (q) => q.mode === "blocking" && q.expires
  && new Date(q.expires).getTime() - Date.now() < EXPIRING_MS;
const kindOf = (q) => (q.meta ? "meta" : q.mode);
// audit decisions reference findings/decisions by id (F63, D14) — make those clickable,
// but ONLY in the audit's own voice (meta items); elsewhere a bare "D1" is a false positive
// Render the question text as MARKDOWN. Meta (self-audit) decisions carry the report's rich
// prose — the title plus a block `detail` (lists, GFM tables, `code`, headings) — so they get
// the block renderer; ordinary blocking/deferred questions are short single-line prompts that
// sit in a flex row, so they keep the inline-only subset. (Before this, open questions rendered
// as raw textContent and answered ones as inline only — so decision markdown never rendered.)
const qBody = (q) => (q.meta ? md(q.question) : mdInline(q.question));
const qText = (q) => {
  const node = el("div", { class: "q-text" }, qBody(q));
  return q.meta ? linkifyRefs(node) : node;
};
const sourceLink = (q) => (q.wizard
  // a clarify session's surface is its run page (D11); a pre-D13 session has none
  ? (q.run_id ? el("a", { href: `#/run/${q.run_id}` }, "new-routine setup")
              : el("span", { class: "muted" }, "new-routine setup"))
  : el("a", { href: `#/routine/${q.routine}` }, q.routine));

export async function render(view, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / decisions"),
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
    const open = state.items.filter((q) => !q.snoozed);
    const counts = { all: open.length, snoozed: state.items.length - open.length };
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
    if (state.sort === "priority") {
      let i = 0;
      for (const [label, match] of GROUPS) {
        const members = qs.filter(match);
        if (!members.length) continue;
        list.append(el("div", { class: "q-group-head" },
          el("span", {}, label), el("span", { class: "q-group-count" }, String(members.length))));
        for (const q of members) list.append(item(q, i++));
      }
    } else {
      qs.forEach((q, i) => list.append(item(q, i)));
    }
    if (focus) focusAt(0);
  }

  async function load({ focus = true } = {}) {
    try { state.items = await api("/api/questions"); }
    catch (err) { list.replaceChildren(emptyState("✕", "Couldn't load decisions", err.message)); return; }
    renderList({ focus });
  }

  function item(q, index) {
    // Already answered (the inbox file exists; the routine consumes it on its next turn/run):
    // show the settled state instead of re-asking — reloads must not resurrect it as open.
    if (q.answered) {
      return el("div", { class: "panel question-item answered" },
        el("div", { class: "q-meta" },
          q.wizard ? chip("wizard", "meta") : q.meta ? chip("meta", "meta") : null,
          q.type === "util-approval" ? chip("util approval", "partial") : null,
          chip(`answered${q.answer_source && q.answer_source !== "web" ? ` via ${q.answer_source}` : ""} · queued`, "ok"),
          sourceLink(q),
          q.asked ? el("span", {}, "asked ", when(q.asked)) : null),
        qText(q),
        el("div", { class: "flow-note mt" },
          el("span", {}, `“${q.answer}” → inbox → consumed by the ${q.mode === "blocking" ? "waiting run" : "next run"}`)));
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
        } catch (err) { toast(err.message, 4000, { error: true }); lifecycle.disabled = false; }
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
          } catch (err) { toast(err.message, 4000, { error: true }); }
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
          } catch (err) { toast(err.message, 4000, { error: true }); lifecycle.value = ""; }
        };
      }
    }
    const form = answerForm(q, {
      control: "input",
      placeholder: "your answer…  (↵ to send)",
      numbered: true,
      defaultLine: false,          // the panel body renders the default line below
      onArrow: (d) => focusAt(index + d),
      submitText: (text) => api(`/api/questions/${q.qid}/answer`,
        { method: "POST", body: { text } }),
      toastText: () => (q.mode === "blocking" ? "answered — the run resumes"
        : q.meta ? "recorded — the next self-audit run acts on it"
        : "answered — the next run picks it up"),
      // Mark answered in place: a deferred question's pending file is only consumed when
      // its routine next runs, so a reload would still list it — that would read as
      // "didn't work".
      onSuccess: (text) => {
        panel.classList.remove("warn");
        controls.replaceChildren(el("div", { class: "flow-note" },
          chip("answered · queued", "ok"),
          el("span", {}, `“${text}” → inbox → consumed by the ${q.mode === "blocking" ? "waiting run" : "next run"}`)));
        state.items = state.items.filter((x) => x.qid !== q.qid);
        syncToolbar();
        inputs.splice(inputs.indexOf(form.input), 1);
        focusAt(index);          // move on to the next open question
      },
      extraControls: lifecycle,
    });
    inputs.push(form.input);
    const controls = el("div", {}, form.node);
    // Config bridge: a revise run can't edit routine.yaml, so it proposes the change as a
    // config_patch on the decision; approving it here PATCHes the routine and resolves the ask.
    const configBar = (q.config_patch && !q.meta) ? (() => {
      const btn = el("button", { class: "btn small primary" }, "approve & apply");
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          await api(`/api/routines/${q.routine}`, { method: "PATCH", body: q.config_patch });
          await api(`/api/questions/${q.qid}/answer`,
            { method: "POST", body: { text: "approved & applied the proposed config change" } });
          toast("config change applied to the routine");
          panel.classList.remove("warn");
          controls.replaceChildren(el("div", { class: "flow-note" },
            chip("applied", "ok"), el("span", {}, "the config change was applied to the routine")));
          state.items = state.items.filter((x) => x.qid !== q.qid);
          syncToolbar();
        } catch (err) { toast(err.message, 5000, { error: true }); btn.disabled = false; }
      };
      return el("div", { class: "flow-note mt" },
        el("div", { class: "small", style: "margin-bottom:4px" },
          "proposed config change — a run can't edit routine.yaml, so approve it here:"),
        el("pre", { class: "doc", style: "margin:0 0 6px;white-space:pre-wrap" },
          JSON.stringify(q.config_patch, null, 2)),
        btn);
    })() : null;
    const panel = el("div", { class: `panel question-item${q.mode === "blocking" ? " warn" : ""}` },
      el("div", { class: "q-meta" },
        expiringSoon(q) ? chip("expiring", "failed") : null,
        q.wizard ? chip("wizard", "meta") : q.meta ? chip("meta", "meta") : null,
        q.type === "util-approval" ? chip("util approval", "partial") : null,
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
  const onBus = () => load({ focus: false }).catch(() => {});
  window.addEventListener("rsched-bus", onBus);
  return () => window.removeEventListener("rsched-bus", onBus);
}
