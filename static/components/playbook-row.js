// The conversation's playbook controls, with D175's OFFER.
//
// Save-as-playbook has always been available here; what the operator decided on 2026-10-10
// (option c, "keep it and make it earn its place") is that the surface should ASK when the
// conversation actually did substantial work — "so the corpus grows from use rather than from
// intent". The server decides whether to ask and sends its own reason line
// (src/rsched/web/playbook_offer.py); this component never recomputes the threshold, so the
// sentence a user reads and the predicate tests/test_playbook_offer.py pins are one thing.
//
// Extracted from views/conversations.js, which is over the house line bar and may only shrink.

import { api } from "/static/api.js";
import { el, toast, toastError } from "/static/util.js";

export function playbookRow(slug, detail) {
  const savePb = el("button", { class: "btn small ghost",
    title: "distil this conversation into a reusable playbook" }, "＋ save as playbook");
  savePb.onclick = async () => {
    savePb.disabled = true;
    toast("distilling a playbook — a few seconds…");
    try {
      const r = await api(`/api/conversations/${slug}/playbook`, { method: "POST" });
      toast(`saved playbook “${r.slug}”${r.axis ? `  ·  varies: ${r.axis}` : ""}`, 6000);
    } catch (err) { toastError(err, 6000); }
    savePb.disabled = false;
  };
  const row = el("div", { class: "row", style: "gap:8px;margin-top:6px;flex-wrap:wrap" }, savePb);

  // The offer. Absent or `show: false` leaves exactly the always-available button that was
  // here before — an offer is a prompt at the right moment, never a nag at every one.
  const pbOffer = detail.playbook_offer || null;
  if (pbOffer && pbOffer.show) {
    savePb.classList.remove("ghost");
    row.prepend(el("div", { class: "muted small", style: "flex-basis:100%",
      title: `${pbOffer.turns} turns of work, offered from ${pbOffer.threshold}` },
      `This conversation did real work — ${pbOffer.reason}?`));
  }

  // Seeded from a playbook → also offer to fold these deltas back into THAT playbook, rather
  // than growing the corpus with a near-duplicate (which is what `mode: "update"` means).
  if (detail.playbook) {
    const updPb = el("button", { class: "btn small ghost",
      title: `revise the “${detail.playbook}” playbook from this conversation` },
      `⟳ update playbook: ${detail.playbook}`);
    updPb.onclick = async () => {
      updPb.disabled = true;
      toast("revising the playbook…");
      try {
        const r = await api(`/api/conversations/${slug}/playbook`, { method: "PUT" });
        toast(`updated playbook “${r.slug}”`, 6000);
      } catch (err) { toastError(err, 6000); }
      updPb.disabled = false;
    };
    row.append(updPb);
  }
  return row;
}
