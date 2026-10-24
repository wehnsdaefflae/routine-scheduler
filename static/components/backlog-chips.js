// The Messages page's headline number, decomposed. `open`+`in_progress` spans a WORKLIST (items
// someone must act on), a FEED (unread summaries, which come back every time any routine
// finishes), rows FOLDED into another report, and reports IN TRANSIT to a routine that has not
// run yet. As one figure it can never reach zero by working the backlog, so a steady worklist
// reads as a growing one. And the worklist itself only falls when its OWNER acts: on 2026-10-18
// it read 100 with one item the operator could move, so the chips say whom it is waiting on.
// The numbers are the server's (`item_counts.counts`); nothing here re-derives them.

import { el, tagChip } from "/static/util.js";

export const ACTIVE = "open,in_progress";

// `operator` is the one owner that is a person, and on this page that person is the reader.
const OWNER_LABEL = { operator: "you" };

export function backlogChips(active, filters, { pick, pickOwner }) {
  const label = active.unread ? `active ${active.worklist || 0} · unread ${active.unread}`
    : `active ${active.worklist || 0}`;
  const out = [tagChip(label,
    { active: filters.status === ACTIVE, onClick: () => pick("status", ACTIVE) })];
  // Folded rows are off the list, so the ONLY way to know they exist is to say so. The count
  // is their own — never added to `active`, or the chip would be lying again.
  if (active.folded)
    out.push(tagChip(`folded ${active.folded}`,
      { active: filters.folded === "1", onClick: () => pick("folded", "1") }));
  // Addressed to a routine that is GONE: no inbox, no run, nobody who could ever drain it. On
  // the worklist it read as live work waiting on an owner who does not exist (F738), so it is
  // counted apart — and said out loud, because only a person can re-route or retract the row.
  if (active.unroutable) {
    const chip = tagChip(`unroutable ${active.unroutable}`);
    chip.title = "reports addressed to a routine this instance no longer has — no inbox to "
      + "deliver into and no run that could read them, so nobody can drain them: re-route or "
      + "retract each one";
    out.push(chip);
  }
  if (active.in_transit) {
    const chip = tagChip(`in transit ${active.in_transit}`);
    chip.title = "reports addressed to a routine that has not run since they were filed — "
      + "its next scheduled run drains them, and nobody can act on them before that";
    out.push(chip);
  }
  const owners = Object.entries(active.by_owner || {});
  if (owners.length) {
    out.push(el("span", { class: "lbl", style: "margin-left:10px" }, "waiting on"));
    for (const [owner, n] of owners)
      out.push(tagChip(`${OWNER_LABEL[owner] || owner} ${n}`,
        { active: filters.owner === owner, onClick: () => pickOwner(owner) }));
  }
  return out;
}
