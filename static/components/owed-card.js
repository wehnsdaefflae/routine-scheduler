// The Decisions page's OWED-TO-YOU card — an obligation a routine carries whose next move is the
// operator's: its last run left it unmet with `b<n> waits on operator: <his act>`
// (engine/accounting.py), and the daily owed-work sweep filed this card (daemon/owed_watch.py).
// Three decisions, executed by web/api_owed_obligation.py: it is DONE, DROP it (no longer
// wanted), or STILL WANTED (he will act; the next card waits a week). The first two close the
// obligation on his word — the routine is told in its inbox — so no run has to.

import { api } from "/static/api.js";
import { el, toast, toastError, when } from "/static/util.js";

export const OWED = "owed-obligation";

export function owedRow(rec, { reload, onChanged } = {}) {
  const f = rec.fields || {};
  const note = el("input", { class: "small", placeholder: "note to the routine (optional)",
    "aria-label": "note to the routine" });
  const buttons = [["met", "it's done", "primary"], ["drop", "drop it", ""],
    ["keep", "still wanted", "ghost"]].map(([choice, label, tone]) =>
    el("button", { class: `btn small ${tone}`.trim(), "data-choice": choice }, label));
  const act = async (choice, said) => {
    for (const b of buttons) b.disabled = true;
    try {
      await api(`/api/pending-creations/${rec.id}/materialize`,
        { method: "POST", body: { choice, note: note.value.trim() } });
      toast(said, 5000); await reload?.(); onChanged?.();
    } catch (err) { toastError(err, 5000); for (const b of buttons) b.disabled = false; }
  };
  const said = { met: "marked done", drop: "dropped", keep: "kept — the card returns in a week" };
  for (const b of buttons) b.onclick = () => act(b.dataset.choice, `${f.id} ${said[b.dataset.choice]}`);
  return el("div", { class: "card mt", "data-owed": `${f.routine}/${f.id}` },
    el("div", { class: "row", style: "gap:10px;align-items:center;flex-wrap:wrap" },
      el("span", {}, el("a", { href: `#/routine/${f.routine}` }, f.routine), " · ",
        el("strong", {}, f.id || "?"), ` · asked ${f.since || "?"}, ${f.days} days`),
      el("span", { style: "margin-left:auto" }), ...buttons),
    el("div", { class: "small mt" }, f.text || ""),
    f.remains ? el("div", { class: "small mt" }, el("strong", {}, "waits on you: "), f.remains)
      : null,
    el("div", { class: "row mt", style: "gap:8px;align-items:center" }, note,
      el("span", { class: "faint small" }, `waiting on you since ${f.waits_since || "?"} · `,
        when(rec.created_at))));
}
