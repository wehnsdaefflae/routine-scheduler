// The Messages page's ORPHAN BANNER: the three ways work leaves this ledger without becoming an
// open item anywhere. Above the list because all three are invisible to every filter below it —
// a deferred piece never became an item at all (that is how D98's stopping panel was lost for
// six days), an undelivered report is addressed to a routine that has never heard of it, and a
// prose-only closure leaves a live row that reads as attended to (readmodels/orphans.py,
// readmodels/prose_closures.py).
//
// Its own module since F793 added that third group: views/messages.js was AT its size ceiling
// with zero slack, so the extraction came first and the group second — the same move
// obs-background.js and questions-taxonomy.js made. The page keeps the list, its filters and its
// composer; this keeps the banner, which has a different source (`GET /api/items/orphans`), its
// own load cycle and its own write (the per-row discard).
//
// Each group filters by its OWN kind, never by "not the other one": the negative filter this
// replaced put every new kind into the deferral group, where it threw on a field deferrals have
// and it does not.

import { api } from "/static/api.js";
import { refHref } from "/static/components/reflinks.js";
import { el, toast, toastError } from "/static/util.js";

/** The banner element — hidden while there is nothing to say, and loaded on creation. */
export function orphanBanner() {
  const box = el("div", { hidden: true });

  const group = (title, rows, card) => {
    if (!rows.length) return null;
    return el("div", {},
      el("div", { class: "q-group-head" },
        el("span", {}, title),
        el("span", { class: "q-group-count" }, String(rows.length))),
      ...rows.map(card));
  };

  // An undelivered orphan can never be delivered (no run has a message to drain), so the only
  // action is to clear it: discard marks it dropped, off this banner and out of the backlog.
  const discard = (o) => {
    const b = el("button", { class: "btn small ghost",
      title: "discard this orphan — marks it dropped, off the banner and out of the backlog" },
      "discard");
    b.onclick = async () => {
      b.disabled = true;
      try {
        await api(`/api/items/orphans/${encodeURIComponent(o.id)}/discard`, { method: "POST" });
        toast(`${o.id} discarded — dropped from the backlog`);
        await load();
      } catch (err) { toastError(err); b.disabled = false; }
    };
    return b;
  };

  const deferralCard = (o) => el("div", { class: "card mt" },
    el("div", {}, el("strong", {}, o.source_ids.join(", ")),
      " was deferred into ", el("a", { href: refHref(o.carrier) }, o.carrier),
      `, which closed ${o.carrier_status} without naming it.`),
    el("div", { class: "faint small mt" }, o.promise));

  const undeliveredCard = (o) => el("div", { class: "card mt" },
    el("div", { class: "row",
      style: "justify-content:space-between;gap:8px;align-items:flex-start" },
      el("div", {}, el("strong", {}, o.id), " from ", el("span", { class: "mono" }, o.from),
        " is addressed to ", el("span", { class: "mono" }, o.target),
        o.target_installed ? ", whose inbox holds no message for it."
                           : ", which is not a routine on this instance."),
      discard(o)),
    el("div", { class: "prose mt" }, o.title));

  // F793. The claim is checked once more, where someone who is NOT the author of the closure can
  // see it: the filing warning reached only the sender, who had just read his own prose and was
  // satisfied. The owner is named because a row is disposed of only for the routine it was
  // delivered TO — nobody else's `settles` can end it — and the claiming words are quoted so the
  // reader judges the actual claim.
  const proseClosureCard = (o) => el("div", { class: "card mt" },
    el("div", {}, el("a", { href: refHref(o.id) }, el("strong", {}, o.id)),
      " is still open: ", el("a", { href: refHref(o.claimed_by) }, o.claimed_by),
      " reads settled and its words say it disposed of this row, while its fields name none of it",
      o.owner ? el("span", {}, " — only ", el("span", { class: "mono" }, o.owner),
        " can settle it.") : "."),
    el("div", { class: "prose mt" }, o.title),
    el("div", { class: "faint small mt" }, `claimed by ${o.claimed_by}: “${o.claim}”`));

  async function load() {
    let rows;
    try { rows = await api("/api/items/orphans"); } catch { return; }
    if (!rows?.length) { box.hidden = true; box.replaceChildren(); return; }
    const of = (kind) => rows.filter((o) => o.kind === kind);
    box.hidden = false;
    box.replaceChildren(...[
      group("deferred, then lost — a carrier item closed without delivering these",
        of("deferral"), deferralCard),
      group("addressed, never delivered — the target has no message for these and never will",
        of("undelivered"), undeliveredCard),
      group("claimed closed in prose, still open — a settled report said it disposed of these",
        of("prose-closure"), proseClosureCard),
    ].filter(Boolean));
  }

  load();
  return box;
}
