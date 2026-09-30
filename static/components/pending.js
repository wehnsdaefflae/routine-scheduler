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
// 3. FINISHED ROUTINES — a routine whose FINISH LINE is reached. This band is the odd one out;
//    the wording has to say so. The routine has ALREADY stopped running (the scheduler builds
//    no fire entry for a routine whose finish line is reached — derived, nothing written), so
//    neither button is what stops it. "Retire it" makes that permanent by writing
//    `enabled: false`; "not yet" reopens the outcomes that were met, so it resumes. A line the
//    CALENDAR reached cannot be reopened — reopening changes nothing a date decides — so its
//    card sends the reader to the routine's Goal settings to change the date instead. Leaving
//    a card is a real third state, not a delay.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, groupHead, toast, toastError, when } from "/static/util.js";

const DRIFT = "library-drift";
const GOAL = "goal-reached";

const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-`
  + String(d.getDate()).padStart(2, "0");

// The client's copy of engine/finishline.reached_by_calendar: a finish line that stays reached
// whoever reopens it — its `until` has passed, or every outcome is a date whose day has come.
// The server refuses "not yet" on such a line (409) and says why; asking first spares the
// reader a button that can only fail.
function reachedByCalendar(f) {
  const today = isoDay(new Date());
  if (f.until && f.until < today) return true;
  const outcomes = f.outcomes || [];
  return outcomes.length > 0 && outcomes.every((o) => o.judge === "date" && o.date
    && o.date <= today);
}

// Who decided one outcome, in the words the routine page's finish line uses.
function outcomeState(o) {
  if (o.judge === "date") return `its date: ${o.date || "none set"}`;
  if (o.status !== "met") return "still open";
  return o.judge === "you" ? "you judged it reached" : "a run proved it";
}

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
    return [el("strong", {}, rec.routine || "?"), " reached its finish line",
      f.why ? ` — ${f.why}` : ""];
  }
  const what = f.name || f.target || "";
  return [`lane: `, el("strong", {}, f.verb || "?"), what ? ` ${what}` : ""];
}

// The full proposal, collapsed — the instruction a routine would be BORN with is the thing
// worth reading before approving, and it is far too long for a row.
function details(rec, openDrift = true) {
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
    return el("details", { class: "small mt", open: openDrift || null },
      el("summary", { style: "cursor:pointer;color:var(--ink-2)" },
        openDrift ? "what broke" : `what broke · ${String((rec.fields || {}).node?.id || "")}`),
      body);
  }
  if (rec.kind === GOAL) {
    // The EVIDENCE is what to read before agreeing a job is over: each outcome in the
    // operator's own words, who decided it, what the proving run said and which run that was.
    // A `disputed` note is a transcript check's standing objection to a claim the run
    // re-asserted — the most important thing on the card when it is there, so it is not folded
    // away with the rest.
    for (const o of f.outcomes || []) {
      body.append(el("div", { class: "mt", "data-goal-outcome": o.id || "" },
        el("code", {}, `[${o.id}]`), " ", o.text || "",
        el("span", { class: "faint" }, ` \u00b7 ${outcomeState(o)}`),
        o.evidence ? el("div", { class: "faint" }, `the run said: ${o.evidence}`) : null,
        o.met_run ? el("div", { class: "faint" }, "proved in ",
          el("a", { href: `#/run/${o.met_run}` }, o.met_run)) : null,
        o.disputed
          ? el("div", { class: "err-text" },
            `\u26a0 a check of the transcript objected: ${o.disputed}`)
          : null));
    }
    if (f.until) body.append(el("div", { class: "mt faint" }, `stop scheduling after ${f.until}`));
    if (!(f.outcomes || []).length && !f.until) {
      body.append(el("div", { class: "faint" }, "no outcomes"));
    }
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

  function band(noun, explain, recs, make) {
    const box = el("div", {}, groupHead(noun, recs.length, explain));
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
      host.append(band("Finished",
        "these routines reached their finish line and have stopped running",
        goals, goalRow));
    }
    if (creations.length) {
      host.append(band("Queued creations",
        "a run proposed these; nothing exists until you approve",
        creations, row));
    }
    if (drift.length) {
      host.append(band("Library drift",
        "a library change broke a routine that holds it; the fix is on the routine",
        driftGroups(drift), driftCard));
    }
  }

  // Neither button stops the routine — it is already stopped. One makes that permanent; the
  // other undoes it. Saying which is which on the card is the whole job of this row.
  function goalRow(rec) {
    const f = rec.fields || {};
    const retire = el("button", { class: "btn small primary" }, "retire it");
    // A line the calendar reached stays reached whoever reopens it: its way back is a new date.
    const byCalendar = reachedByCalendar(f);
    const back = byCalendar
      ? el("a", { class: "btn small", "data-goal-date": "",
        href: `#/routine/${rec.routine}?section=goal`,
        title: "the calendar reached this finish line, so reopening it changes nothing — "
          + "move its date in the routine's Goal settings to keep it going" }, "change its date")
      : el("button", { class: "btn small" }, "not yet");
    const act = async (fn) => {
      retire.disabled = back.disabled = true;
      try { await fn(); await load(); onChanged?.(); }
      catch (err) { toastError(err, 5000);
        retire.disabled = back.disabled = false; }
    };
    retire.onclick = () => act(async () => {
      if (!(await confirmDialog(
        `Retire ${rec.routine}? It has already stopped running; this writes enabled: false so it `
        + "stays off even if its finish line is reopened later. Its runs, its finish line and "
        + "its history all stay readable; you can switch it back on any time.",
        { confirmLabel: "retire it" }))) throw new Error("");
      await api(`/api/pending-creations/${rec.id}/materialize`, { method: "POST" });
      toast(`${rec.routine} retired — switched off, nothing deleted`, 5000);
    });
    if (!byCalendar) {
      back.onclick = () => act(async () => {
        const r = await api(`/api/pending-creations/${rec.id}/discard`,
          { method: "POST", body: { reason: "the finish line is not reached" } });
        toast((r.reopened || []).length
          ? `finish line reopened (${r.reopened.join(", ")}) — ${rec.routine} is scheduled again`
          : "discarded", 5000);
      });
    }
    return el("div", { class: "card mt", "data-goal": rec.id },
      el("div", { class: "row", style: "gap:10px;align-items:center" },
        el("span", {}, ...summarize(rec)),
        el("span", { style: "margin-left:auto" }),
        el("a", { class: "btn small", href: `#/routine/${rec.routine}` }, "open"),
        retire, back),
      el("div", { class: "faint small" },
        "it has already stopped running \u00b7 ",
        rec.run_id
          ? el("span", {}, "reached in ", el("a", { href: `#/run/${rec.run_id}` }, rec.run_id))
          : "no run involved",
        " \u00b7 ", when(rec.created_at)),
      details(rec));
  }

  // ONE LIBRARY COMMIT IS ONE EVENT, however many permissions it took away. The watcher files
  // one record per GAP, so a commit that cost a routine two capabilities produced two cards
  // with the same routine, the same commit and the same "what broke" — and the reader had to
  // notice they were one thing. Grouped by routine + commit, the card says "lost 2" and lists
  // both; dismissing it settles every record it stands for, which is what the reader means.
  function driftGroups(recs) {
    const by = new Map();
    for (const rec of recs) {
      const key = `${rec.routine}\u0000${(rec.fields || {}).head || ""}`;
      if (!by.has(key)) by.set(key, []);
      by.get(key).push(rec);
    }
    return [...by.values()];
  }

  // Nothing to materialize: a drift record is a NOTICE. It links to the routine whose setup the
  // change broke (where the surface panel shows the same row with its fix) and is dismissed
  // once seen — a dismissal is not a re-notify loop.
  function driftCard(group) {
    const first = group[0];
    const seen = el("button", { class: "btn small" },
      group.length > 1 ? `dismiss all ${group.length}` : "dismiss");
    seen.onclick = async () => {
      seen.disabled = true;
      try {
        for (const rec of group) {
          await api(`/api/pending-creations/${rec.id}/discard`,
            { method: "POST", body: { reason: "drift acknowledged" } });
        }
        await load(); onChanged?.();
      } catch (err) { toastError(err, 5000); seen.disabled = false; }
    };
    const lost = group.map((rec) => (rec.fields || {}).node?.id
      || (rec.fields || {}).entity || "?");
    const head = String((first.fields || {}).head || "").slice(0, 8);
    return el("div", { class: "card mt", "data-drift": first.id,
                       "data-drift-count": String(group.length) },
      el("div", { class: "row", style: "gap:10px;align-items:center" },
        el("span", {}, el("strong", {}, first.routine || "?"),
          ` lost ${lost.length} `, lost.length === 1 ? "permission" : "permissions",
          head ? ` after ${head}` : ""),
        el("span", { style: "margin-left:auto" }),
        el("a", { class: "btn small primary", href: `#/routine/${first.routine}` }, "open the routine"),
        seen),
      el("div", { class: "row", style: "gap:6px;flex-wrap:wrap" },
        ...lost.map((id) => el("code", { class: "small" }, id))),
      el("div", { class: "faint small" }, "found by the library watcher · ", when(first.created_at)),
      // One gap: the diagnosis is the card. Several: each is named and folded.
      ...group.map((rec) => details(rec, group.length === 1)));
  }

  function row(rec) {
    const make = el("button", { class: "btn small primary" }, "create it");
    const drop = el("button", { class: "btn small danger" }, "discard");
    const act = async (btn, fn) => {
      make.disabled = drop.disabled = true;
      try { await fn(); await load(); onChanged?.(); }
      catch (err) { toastError(err, 5000);
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
