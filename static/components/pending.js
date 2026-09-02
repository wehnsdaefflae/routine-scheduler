// The Decisions page's two PENDING bands — records a run or the daemon filed that nobody has
// decided yet. Bands of their own rather than rows blended into the decision list, because
// neither is a question: no answer text, no options, two buttons each.
//
// 1. QUEUED CREATIONS (F328) — what a scheduled run proposed: a routine to create, or a change
//    to a routine group. Materializing goes through the web's one config-writing path.
// 2. LIBRARY DRIFT — filed by `daemon/library_watch.py` when a library commit newly BLOCKS a
//    routine that holds the changed document. Nothing proposed it and nothing can materialize
//    it: the fix is on the routine, so the record links there and is dismissed once seen.
//    These were filed from the day the watcher shipped and rendered as a malformed creation
//    row ("group: ?") with a "create it" button that could only ever 400.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, toast, when } from "/static/util.js";

const DRIFT = "library-drift";

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
  const what = f.name || f.target || "";
  return [`group: `, el("strong", {}, f.verb || "?"), what ? ` ${what}` : ""];
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
    const creations = recs.filter((r) => r.kind !== DRIFT);
    host.replaceChildren();
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
      toast(r.slug ? `created routine “${r.slug}”` : "group change applied", 5000);
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
