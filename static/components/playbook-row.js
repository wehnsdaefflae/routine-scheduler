// The conversation's playbook CAPTURE surfaces: the always-present controls under the composer
// (with D175's offer) and D307's offer under a conversation's FINAL reply.
//
// Save-as-playbook has always been available here; what the operator decided on 2026-10-10
// (option c, "keep it and make it earn its place") is that the surface should ASK when the
// conversation actually did substantial work — "so the corpus grows from use rather than from
// intent". He then decided (code review, D307) that it must also ask where a conversation ENDS:
// the reply that declares itself final is the moment the procedure is complete and a person has
// just read that it is done. Both asks are the SAME server predicate
// (src/rsched/web/playbook_offer.py), which says `show` and, in `why`, WHICH reason fired —
// `turns` for the composer row, `final` for the reply offer — so the two never ask at once and
// this component never recomputes a threshold.
//
// The SENTENCE a user reads is COMPOSED here — the lead-in and the question mark below, the
// server's fragment inside it — so it exists in neither file, and the server-side predicate
// cannot see this half. tests/ui/test_playbook_offer_sentence.py and
// tests/ui/test_final_offer.py assert the composed sentences in the browser, which is the only
// side that sees both (R3645).
//
// Extracted from views/conversations.js, which is over the house line bar and may only shrink —
// which is also why the final-reply offer lives here beside the save/update call it reuses,
// rather than in the chat renderer (static/components/chat.js, likewise at its ceiling).

import { api } from "/static/api.js";
import { el, toast, toastError } from "/static/util.js";

/** POST (save) or PUT (update) the conversation's playbook, reporting through the toast.
 *
 *  The one call both surfaces make: `POST /api/conversations/{slug}/playbook` distils a NEW
 *  playbook, `PUT` folds this conversation back into the one it was seeded from. The button is
 *  disabled while the system model works (several seconds) so a second click cannot start a
 *  second distillation of the same conversation.
 */
async function savePlaybook(slug, btn, { update }) {
  btn.disabled = true;
  toast(update ? "revising the playbook…" : "distilling a playbook — a few seconds…");
  try {
    const r = await api(`/api/conversations/${slug}/playbook`,
                        { method: update ? "PUT" : "POST" });
    toast(update ? `updated playbook “${r.slug}”`
                 : `saved playbook “${r.slug}”${r.axis ? `  ·  varies: ${r.axis}` : ""}`, 6000);
  } catch (err) { toastError(err, 6000); }
  btn.disabled = false;
}

export function playbookRow(slug, detail) {
  const savePb = el("button", { class: "btn small ghost",
    title: "distil this conversation into a reusable playbook" }, "＋ save as playbook");
  savePb.onclick = () => savePlaybook(slug, savePb, { update: false });
  const row = el("div", { class: "row", style: "gap:8px;margin-top:6px;flex-wrap:wrap" }, savePb);

  // The offer. Absent, `show: false`, or a `why` that belongs to the OTHER surface leaves
  // exactly the always-available button that was here before — an offer is a prompt at the
  // right moment, never a nag at every one, and never the same prompt in two places.
  const pbOffer = detail.playbook_offer || null;
  if (pbOffer && pbOffer.show && pbOffer.why === "turns") {
    savePb.classList.remove("ghost");
    row.prepend(el("div", { class: "muted small", style: "flex-basis:100%",
      "data-pb-offer": true,          // the hook the browser case selects the sentence on
      title: `${pbOffer.turns} turns of work, offered from ${pbOffer.threshold}` },
      `This conversation did real work — ${pbOffer.reason}?`));
  }

  // Seeded from a playbook → also offer to fold these deltas back into THAT playbook, rather
  // than growing the corpus with a near-duplicate (which is what `mode: "update"` means).
  if (detail.playbook) {
    const updPb = el("button", { class: "btn small ghost",
      title: `revise the “${detail.playbook}” playbook from this conversation` },
      `⟳ update playbook: ${detail.playbook}`);
    updPb.onclick = () => savePlaybook(slug, updPb, { update: true });
    row.append(updPb);
  }
  return row;
}

/** D307: the offer under the conversation's FINAL reply.
 *
 *  The composer row above asks a conversation that did real work and may well do more. This
 *  asks at the ENDING: the server saw the newest reply declare `final: true` on a terminal
 *  conversation (`why: "final"`), which is the moment the procedure is whole. It is mounted
 *  directly below the chat, so it reads as the line under that last reply, and it carries the
 *  same save/update call as the button under the composer — the control stays there for the
 *  conversations neither reason covers.
 *
 *  Returns `{ node, refresh }`. `node` is an empty wrapper until something is offered, so the
 *  view can mount it once; `refresh` re-reads the conversation detail, because a reply that
 *  finishes while the page is open ends the conversation without a remount (the live tail
 *  carries the events, not the detail payload).
 */
export function finalOffer(slug, detail) {
  const node = el("div", {});

  const paint = (d) => {
    const pbOffer = d.playbook_offer || null;
    const asked = Boolean(pbOffer && pbOffer.show && pbOffer.why === "final");
    // Stamped whether or not anything is offered: it tells a reader (and the browser case
    // asserting ABSENCE) that the paint ran and what the server's verdict was.
    node.setAttribute("data-final-offer", asked ? "final" : (pbOffer?.why || "none"));
    if (!asked) { node.replaceChildren(); return; }
    // EXACTLY ONE ask, on a live page too. A fresh detail read cannot offer both (the server's
    // `why` is single-valued), but the composer row is painted ONCE at mount: a conversation
    // that was already terminal with turns over the bar carries that line, and a further
    // message ending in a final reply would add this one beside it. The ending supersedes it,
    // so the superseded line goes — it is re-derived from the server on the next mount.
    document.querySelector("[data-pb-offer]")?.remove();
    const update = pbOffer.mode === "update";
    const btn = el("button", { class: "btn small",
      title: update ? `revise the “${d.playbook}” playbook from this conversation`
                    : "distil this conversation into a reusable playbook" },
      update ? `⟳ update playbook: ${d.playbook}` : "＋ save as playbook");
    btn.onclick = () => savePlaybook(slug, btn, { update });
    node.replaceChildren(el("div", { class: "pb-final" },
      el("div", { class: "pb-final-ask", "data-pb-final": true },
        `This conversation is done — ${pbOffer.reason}?`),
      btn));
  };

  paint(detail);
  return {
    node,
    /** Re-read the detail and repaint — the conversation just became terminal. */
    async refresh() {
      try { paint(await api(`/api/conversations/${slug}`)); }
      catch { /* a failed read leaves what is already rendered; the next one repaints */ }
    },
  };
}
