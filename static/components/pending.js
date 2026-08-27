// Queued creations on the Decisions page (F328): what a scheduled run proposed and nobody has
// decided yet — a routine to create, or a change to a routine group.
//
// A band of its own rather than rows blended into the decision list, because a proposal is a
// different object from a question: no answer text, no options, two buttons. Blending it in
// would mean special-casing every branch of the decision renderer for an item that shares none
// of its shape.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, toast, when } from "/static/util.js";

function summarize(rec) {
  const f = rec.fields || {};
  if (rec.kind === "create_routine") {
    return [`create routine `, el("code", {}, f.slug || "?"),
      ` from pattern `, el("code", {}, f.workflow || "?")];
  }
  const what = f.name || f.target || "";
  return [`group: `, el("strong", {}, f.verb || "?"), what ? ` ${what}` : ""];
}

// The full proposal, collapsed — the instruction a routine would be BORN with is the thing
// worth reading before approving, and it is far too long for a row.
function details(rec) {
  const f = rec.fields || {};
  const body = el("div", { class: "small mt" });
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
    el("summary", { style: "cursor:pointer;color:var(--muted)" }, "what would be created"), body);
}

export function pendingBand({ onChanged } = {}) {
  const host = el("div", { class: "mt", hidden: true });

  async function load() {
    let recs = [];
    try { recs = await api("/api/pending-creations"); }
    catch { host.hidden = true; return; }
    host.hidden = !recs.length;
    if (!recs.length) { host.replaceChildren(); return; }
    host.replaceChildren(el("div", { class: "q-group-head" },
      el("span", {}, "queued creations — a run proposed these; nothing exists until you approve"),
      el("span", { class: "q-group-count" }, String(recs.length))));
    for (const rec of recs) host.append(row(rec));
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
