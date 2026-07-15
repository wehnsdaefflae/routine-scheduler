// Decisions inbox: everything open across routines — blocking questions, deferred ones,
// and the self-audit report's open decisions (meta badge) — in ONE answering surface.
// Keyboard-first: the first pending item autofocuses, Enter submits, ↑/↓ move, 1–9 pick
// an option. Toolbar: filter by kind or routine, sort by priority/age/routine.
// Priority rank: blocking (a run is waiting) > meta (system-level) > deferred.

import { api } from "/static/api.js";
import { answerForm } from "/static/components/answerform.js";
import { mdInline } from "/static/md.js";
import { chip, el, emptyState, skeleton, toast, when } from "/static/util.js";
import { TERMINAL } from "/static/states.js";

const FILTERS = [["all", "All"], ["blocking", "Blocking"], ["deferred", "Deferred"], ["meta", "Meta"], ["snoozed", "Snoozed"]];
const SNOOZES = [["60", "1 hour"], ["240", "4 hours"], ["1440", "1 day"], ["10080", "1 week"]];
const SORTS = [["priority", "priority"], ["newest", "newest"], ["oldest", "oldest"], ["routine", "routine"]];

const rank = (q) => (q.answered ? 3 : q.mode === "blocking" ? 0 : q.meta ? 1 : 2);
const kindOf = (q) => (q.meta ? "meta" : q.mode);
const sourceLink = (q) => (q.wizard
  ? el("a", { href: `#/wizard/${q.routine}` }, "new-routine wizard")
  : el("a", { href: `#/routine/${q.routine}` }, q.routine));

export async function render(view) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / decisions"),
      el("h1", {}, "Decisions"),
      el("div", { class: "sub" }, "answers the routines need from you — one inbox, blocking first")),
    el("div", { class: "kbd-hint" },
      el("kbd", {}, "↵"), " answer · ", el("kbd", {}, "↑"), el("kbd", {}, "↓"), " move · ",
      el("kbd", {}, "1"), "–", el("kbd", {}, "9"), " pick option")));

  const state = { filter: "all", routine: "", sort: "priority", items: [] };

  const filterChips = new Map();
  const chipRow = el("div", { class: "row", style: "gap:6px" });
  for (const [key, label] of FILTERS) {
    const b = el("button", { class: "btn small" }, label);
    b.onclick = () => { state.filter = key; renderList(); };
    filterChips.set(key, b);
    chipRow.append(b);
  }
  const routineSel = el("select", { class: "small" });
  routineSel.onchange = () => { state.routine = routineSel.value; renderList(); };
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
    if (state.sort === "priority") qs = [...qs].sort((a, b) => rank(a) - rank(b) || byAsked(a, b));
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
    qs.forEach((q, i) => list.append(item(q, i)));
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
        el("div", { class: "q-text" }, mdInline(q.question)),
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
    const panel = el("div", { class: `panel question-item${q.mode === "blocking" ? " warn" : ""}` },
      el("div", { class: "q-meta" },
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
      el("div", { class: "q-text" }, q.question),
      q.default ? el("div", { class: "faint small mt",
        title: "what the routine does if this stays unanswered" },
        `↪ without an answer: ${q.default}`) : null,
      controls);
    return panel;
  }

  await load();
  const onBus = () => load({ focus: false }).catch(() => {});
  window.addEventListener("rsched-bus", onBus);
  return () => window.removeEventListener("rsched-bus", onBus);
}
