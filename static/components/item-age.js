// HOW OLD IS THIS ITEM — the age line on a Messages card (F753).
//
// The card's `origin` line shows where an item came from and when its SOURCE was written. For
// a finding or a decision that date is the `generated` stamp of self-audit's report.json,
// which is rewritten wholesale every run: it moved to "today" every time, so nothing on this
// page could tell you an item's age and "the ten oldest items" was unanswerable. The server
// now carries `first_seen` from a store the rewrite cannot reach (src/rsched/firstseen.py).
//
// Two shapes, and the difference is the honest part:
//
//   exact  — `first_seen_or_earlier` false: the instance watched this item arrive. "14d old"
//            with the date in the title.
//   floor  — true: the item already existed when stamping began, so there IS no true first
//            sight. It renders "14d old or more · since 2026-07-22 or earlier", never a bare
//            date — a reader must not take a floor for a birthday. These are the oldest items
//            on the page, which is exactly where false precision would mislead most.
//
// Its own file because static/views/messages.js is at its size ceiling (425, shrink-only) and
// static/components/itemcard.js mounts one line to call this — the same move obs-background.js
// and obs-accounting.js made out of transcript.js.

import { el } from "/static/util.js";

const DAY_MS = 86400000;

// Whole days between `iso` and now, or null when it is not a date we can read. Days, not the
// relative scale `when()` uses: an item's age is read in days and months, and "3h ago" on a
// maintenance item answers a question nobody asked.
export function ageDays(iso) {
  const t = Date.parse(String(iso || ""));
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / DAY_MS));
}

// "14d" / "3mo" / "2y" — an age at the scale a backlog is read at.
export function ageLabel(days) {
  if (days === null) return "";
  if (days < 60) return `${days}d`;
  if (days < 730) return `${Math.round(days / 30)}mo`;
  return `${(days / 365).toFixed(1)}y`;
}

// The line itself, or null when the server sent no stamp (an older build, or a row whose
// origin carries no date at all — shown as nothing rather than as "0d", which would read as
// "arrived today" and is the same false precision in the other direction).
export function itemAge(item) {
  const iso = item?.first_seen || "";
  const days = ageDays(iso);
  if (days === null) return null;
  const floor = Boolean(item?.first_seen_or_earlier);
  const date = String(iso).slice(0, 10);
  const label = `${ageLabel(days)} old${floor ? " or more" : ""}`;
  return el("div", {
    class: "faint small mt",
    style: "display:flex;gap:8px;flex-wrap:wrap",
    "data-first-seen": String(iso),
    "data-first-seen-floor": floor ? "1" : "0",
    title: floor
      ? `first seen on or before ${iso} — this item already existed when the instance began `
        + "stamping items, so its true first sight is unknown and this is the oldest date it "
        + "can be proven to predate"
      : `first seen ${iso} — the instance watched this item arrive`,
  },
    el("span", {}, "age"),
    el("strong", {}, label),
    el("span", { class: "faint" }, floor ? `since ${date} or earlier` : `since ${date}`));
}
