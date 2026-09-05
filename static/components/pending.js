// The Decisions page's three PENDING bands — records a run or the daemon filed that nobody has
// decided yet. Bands of their own rather than rows blended into the decision list, because none
// is a question: no answer text, no options, two buttons each.
//
// 1. QUEUED CREATIONS (F328) — what a scheduled run proposed: a routine to create, or a change
//    to a fire lane. Materializing goes through the web's one config-writing path.
// 2. LIBRARY DRIFT — filed by `daemon/library_watch.py` when a library commit newly BLOCKS a
//    routine that holds the changed document. Nothing proposed it and nothing can materialize
//    it: the fix is on the routine, so the record links there and is dismissed once seen.
//    These were filed from the day the watcher shipped and fell through summarize() to the
//    lane-proposal label with an unknown verb ("lane: ?"), carrying a "create it" button that
//    could only ever 400 — which is why every kind here is matched before that fallback.
// 3. FINISHED ROUTINES — a routine reporting that its FINAL GOAL is met. This band is the odd
//    one out, and the wording has to say so: the routine has ALREADY stopped running (the
//    scheduler builds no fire entry for a routine whose goal is satisfied — derived, nothing
//    written), so neither button is what stops it. "Retire it" makes that permanent by writing
//    `enabled: false`; "not yet" reopens the goal and it resumes. Leaving it is a real third
//    state, not a delay.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, toast, when } from "/static/util.js";

const DRIFT = "library-drift";
const GOAL = "goal-reached";

function summarize(rec) {
  const f = rec.fields || {};
  if (rec.kind === "create_routine") {
    return [`create routine `, el("code", {}, f.slug || "?"),
      ` from pattern `, el("code", {}, f.workflow || "?")];
  }
  if (rec.kind === DRIFT) {
    const node = f.node || {};
    return [el("strong", {}, rec.routine || "?"), " lost ", el("code", {}, node.id || f.entity || "?")];
  }
  if (rec.kind === GOAL) {
    const n = (f.conditions || []).length;
    return [el("strong", {}, rec.routine || "?"), " reports its final goal met",
      n ? ` — ${n} condition${n === 1 ? "" : "s"}` : ""];
  }
  const what = f.name || f.target || "";
  return [`lane: `, el("strong", {}, f.verb || "?"), what ? ` ${what}` : ""];
}

// The full proposal, collapsed — the instruction a routine would be BORN with is the thing
// worth reading before approving, and it is far too long for a row.
function details(rec) {
  const f = rec.fields || {};
  const body = el("div", { class: "small mt" });
  if (rec.kind === DRIFT) {
    const node = f.node || {};
    // .filter(Boolean): append STRINGIFIES a null argument into the text "null" (el() drops
    // null children; append does not).
    body.append(...[
      el("div", {}, node.why || rec.summary || ""),
      node.effect ? el("div", { class: "faint mt" }, node.effect) : null,
      el("div", { class: "faint mt" }, `after library commit ${String(f.head || "").slice(0, 8)}`),
    ].filter(Boolean));
    return el("details", { class: "small mt", open: true },
      el("summary", { style: "cursor:pointer;color:var(--ink-2)" }, "what broke"), body);
  }
  if (rec.kind === GOAL) {
    // The EVIDENCE is what to read before agreeing a job is over: which condition, in the user's
    // own words, what the run said about it, and which run said so. A `disputed` mark is the v2
    // verifier's standing objection to a verdict the run re-asserted — the most important thing
    // on the card when it is there, so it is not folded away with the rest.
    for (const c of f.conditions || []) {
      body.append(el("div", { class: "mt" },
        el("code", {}, `[${c.id}]`), " ", c.text,
        c.note ? el("div", { class: "faint" }, `the run said: ${c.note}`) : null,
        c.resolved_run ? el("div", { class: "faint" }, `met in ${c.resolved_run}`) : null,
        c.disputed
          ? el("div", { class: "err-text" }, `\u26a0 the verifier objected: ${c.disputed}`)
          : null));
    }
    if (!(f.conditions || []).length) body.append(el("div", { class: "faint" }, "no conditions"));
    return el("details", { class: "small mt", open: true },
      el("summary", { style: "cursor:pointer;color:var(--ink-2)" }, "the evidence"), body);
  }
  if (rec.kind === "create_routine") {
    body.append(el("div", { class: "faint" }, `name: ${f.name || "(none)"}`),
      el("pre", { class: "doc mt" }, f.instruction || "(no instruction)"));
  } else {
    const rows = Object.entries(f).filter(([k]) => k !== "verb");
    body.append(rows.length
      ? el("pre", { class: "doc" }, rows.map(([k, v]) =>
          `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join("\n"))
      : el("div", { class: "faint" }, "no fields beyond the verb"));
  }
  return el("details", { class: "small mt" },
    el("summary", { style: "cursor:pointer;color:var(--ink-2)" }, "what would be created"), body);
}

export function pendingBand({ onChanged } = {}) {
  const host = el("div", { class: "mt", hidden: true });

  function band(title, recs, make) {
    const box = el("div", {},
      el("div", { class: "q-group-head" },
        el("span", {}, title),
        el("span", { class: "q-group-count" }, String(recs.length))));
    for (const rec of recs) box.append(make(rec));
    return box;
  }

  async function load() {
    let recs = [];
    try { recs = await api("/api/pending-creations"); }
    catch { host.hidden = true; return; }
    host.hidden = !recs.length;
    if (!recs.length) { host.replaceChildren(); return; }
    const drift = recs.filter((r) => r.kind === DRIFT);
    const goals = recs.filter((r) => r.kind === GOAL);
    const creations = recs.filter((r) => r.kind !== DRIFT && r.kind !== GOAL);
    host.replaceChildren();
    // Finished routines first: this is the only band whose subject has ALREADY changed state.
    if (goals.length) {
      host.append(band(
        "finished — these routines report their final goal met and have stopped running",
        goals, goalRow));
    }
    if (creations.length) {
      host.append(band(
        "queued creations — a run proposed these; nothing exists until you approve",
        creations, row));
    }
    if (drift.length) {
      host.append(band(
        "library drift — a library change broke a routine that holds it; the fix is on the routine",
        drift, driftRow));
    }
  }

  // Neither button stops the routine — it is already stopped. One makes that permanent, the
  // other undoes it. Saying which is which on the card is the whole job of this row.
  function goalRow(rec) {
    const retire = el("button", { class: "btn small primary" }, "retire it");
    const back = el("button", { class: "btn small" }, "not yet");
    const act = async (fn) => {
      retire.disabled = back.disabled = true;
      try { await fn(); await load(); onChanged?.(); }
      catch (err) { toast(err.message, 5000, { error: true });
        retire.disabled = back.disabled = false; }
    };
    retire.onclick = () => act(async () => {
      if (!(await confirmDialog(
        `Retire ${rec.routine}? It has already stopped running; this writes enabled: false so it `
        + "stays off even if a goal condition is later cleared. Its runs, its goal and its "
        + "history all stay readable, and you can switch it back on any time.",
        { confirmLabel: "retire it" }))) throw new Error("");
      await api(`/api/pending-creations/${rec.id}/materialize`, { method: "POST" });
      toast(`${rec.routine} retired — switched off, nothing deleted`, 5000);
    });
    back.onclick = () => act(async () => {
      const r = await api(`/api/pending-creations/${rec.id}/discard`,
        { method: "POST", body: { reason: "the goal is not reached" } });
      toast((r.reopened || []).length
        ? `goal reopened (${r.reopened.join(", ")}) — ${rec.routine} is scheduled again`
        : "discarded", 5000);
    });
    return el("div", { class: "card mt", "data-goal": rec.id },
      el("div", { class: "row", style: "gap:10px;align-items:center" },
        el("span", {}, ...summarize(rec)),
        el("span", { style: "margin-left:auto" }),
        el("a", { class: "btn small", href: `#/routine/${rec.routine}` }, "open"),
        retire, back),
      el("div", { class: "faint small" },
        "it has already stopped running \u00b7 reported in ", rec.run_id || "?", " \u00b7 ",
        when(rec.created_at)),
      details(rec));
  }

  // Nothing to materialize: a drift record is a NOTICE. It links to the routine whose setup the
  // change broke (where the surface panel shows the same row with its fix) and is dismissed
  // once seen — the watcher files one record per gap, so a dismissal is not a re-notify loop.
  function driftRow(rec) {
    const seen = el("button", { class: "btn small" }, "dismiss");
    seen.onclick = async () => {
      seen.disabled = true;
      try {
        await api(`/api/pending-creations/${rec.id}/discard`,
          { method: "POST", body: { reason: "drift acknowledged" } });
        await load(); onChanged?.();
      } catch (err) { toast(err.message, 5000, { error: true }); seen.disabled = false; }
    };
    return el("div", { class: "card mt", "data-drift": rec.id },
      el("div", { class: "row", style: "gap:10px;align-items:center" },
        el("span", {}, ...summarize(rec)),
        el("span", { style: "margin-left:auto" }),
        el("a", { class: "btn small primary", href: `#/routine/${rec.routine}` }, "open the routine"),
        seen),
      el("div", { class: "faint small" }, "found by the library watcher · ", when(rec.created_at)),
      details(rec));
  }

  function row(rec) {
    const make = el("button", { class: "btn small primary" }, "create it");
    const drop = el("button", { class: "btn small danger" }, "discard");
    const act = async (btn, fn) => {
      make.disabled = drop.disabled = true;
      try { await fn(); await load(); onChanged?.(); }
      catch (err) { toast(err.message, 5000, { error: true });
        make.disabled = drop.disabled = false; }
    };
    make.onclick = () => act(make, async () => {
      const r = await api(`/api/pending-creations/${rec.id}/materialize`, { method: "POST" });
      toast(r.slug ? `created routine “${r.slug}”` : "lane change applied", 5000);
    });
    drop.onclick = () => act(drop, async () => {
      if (!(await confirmDialog(
        `Discard this proposal? ${rec.routine} is told, so its next run stops waiting on it.`,
        { confirmLabel: "discard" }))) throw new Error("");
      await api(`/api/pending-creations/${rec.id}/discard`, { method: "POST", body: {} });
      toast("discarded — the proposing routine was told");
    });
    return el("div", { class: "card mt" },
      el("div", { class: "row", style: "gap:10px;align-items:center" },
        el("span", {}, ...summarize(rec)),
        el("span", { style: "margin-left:auto" }),
        make, drop),
      el("div", { class: "faint small" },
        `proposed by ${rec.routine} · ${rec.run_id || ""} · `, when(rec.created_at)),
      details(rec));
  }

  load();
  return { node: host, refresh: load };
}
