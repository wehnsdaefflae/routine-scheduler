// The Decisions page's REPLACEMENT card — one routine rebuilt under a new slug, proved beside the
// one it would replace, waiting for the operator's ONE accept (engine/candidate.py; the swap is
// web/api_pending._materialize_replace).
//
// The card exists so the decision is made on EVIDENCE: both routines' development views are one
// click away, and each LEADS with its Outcome panel — what it produced against what it cost —
// so "is the candidate better?" is read off two numbers, never off either routine's account of
// itself. "swap them" moves the old routine's lane place, schedule and secrets to the candidate
// and switches the old one off (kept, never deleted); "keep the old one" switches the candidate
// off. The proposing routine is told either way.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, toast, toastError, when } from "/static/util.js";

export const SWAP = "replace-routine";

const dev = (slug) => el("a", { href: `#/changes/${encodeURIComponent(slug)}` }, slug);

export function swapRow(rec, { reload, onChanged } = {}) {
  const f = rec.fields || {};
  const swap = el("button", { class: "btn small primary" }, "swap them");
  const keep = el("button", { class: "btn small" }, `keep ${f.replaces || "the old one"}`);
  const act = async (fn) => {
    swap.disabled = keep.disabled = true;
    try { await fn(); await reload?.(); onChanged?.(); }
    catch (err) { toastError(err, 5000); swap.disabled = keep.disabled = false; }
  };
  swap.onclick = async () => {
    if (!(await confirmDialog(
      `Swap ${f.replaces} for ${f.candidate}? ${f.candidate} takes ${f.replaces}'s lane place, `
      + `schedule and secrets; ${f.replaces} is switched off — kept, with all its runs and history.`,
      { confirmLabel: "swap them" }))) return;
    await act(async () => {
      await api(`/api/pending-creations/${rec.id}/materialize`, { method: "POST" });
      toast(`${f.candidate} now runs in ${f.replaces}'s place`, 5000);
    });
  };
  keep.onclick = () => act(async () => {
    await api(`/api/pending-creations/${rec.id}/discard`,
      { method: "POST", body: { reason: `kept ${f.replaces}` } });
    toast(`${f.candidate} switched off — ${f.replaces} keeps running`, 5000);
  });
  return el("div", { class: "card mt", "data-swap": rec.id },
    el("div", { class: "row", style: "gap:10px;align-items:center" },
      el("span", {}, "replace ", el("strong", {}, f.replaces || "?"), " by ",
        el("code", {}, f.candidate || "?")),
      el("span", { style: "margin-left:auto" }), swap, keep),
    el("div", { class: "faint small" }, `proposed by ${rec.routine} · ${rec.run_id || ""} · `,
      when(rec.created_at)),
    f.why ? el("div", { class: "small mt" }, f.why) : null,
    el("div", { class: "small mt" }, "compare what each produced against what it cost: ",
      dev(f.replaces || ""), " · ", dev(f.candidate || "")));
}
