// ONE reader of item SUBJECTS for the whole console: `R3361` → "the Decisions page is an
// inbox sorted by urgency".
//
// The operator, 2026-11-12, on every surface that needs his attention: *"they must be
// self-contained. they cannot have me hunting for content in other documents. i want all info
// i need in the text i have in front of me along with hyperlinks to the files / documents
// where the info is from. worst case scenario is content i need to know about being referenced
// only by an identifier."* His own first cut: wherever the UI renders an id, it renders a link
// whose text is the thing's one-line subject.
//
// `reflinks.js` already made every `F63`/`D14`/`R7` in rendered prose a link — but the link's
// text was the id and its tooltip said "jump to R3361 on the Messages page", which tells him
// where it is and never what it is. This module supplies the missing half.
//
// WHY NO NEW STORE AND NO NEW ROUTE: `/api/items` already returns every row with its `title`.
// One read, cached for the session, shared by every surface — the same shape as
// `questions-store.js`, minus the live cadence, because an item's SUBJECT does not change
// while you look at it (its status does, and nothing here reads status).
//
// Resolution is SYNCHRONOUS by design: prose is rendered inline, often before the read lands.
// So `titleOf` answers from the cache or not at all, and `onTitles` lets the one surface that
// cares — the ref links themselves — fill in afterwards. A ref whose subject never resolves
// keeps the id as its text, which is what it has always been.

import { api } from "/static/api.js";

//: id → one-line subject. Session-scoped: the whole point is to answer without a round trip.
const titles = new Map();
const waiting = new Set();
let loaded = false;
let inflight = null;

/** The subject of `id`, or "" when it is not known (yet, or at all). Never throws, never waits. */
export function titleOf(id) {
  return titles.get(String(id || "")) || "";
}

/** Has the one read landed? Lets a caller tell "no subject" from "not asked yet". */
export function titlesLoaded() {
  return loaded;
}

/** Run `fn` once the subjects are in (immediately if they already are). */
export function onTitles(fn) {
  if (loaded) { fn(); return; }
  waiting.add(fn);
}

/**
 * Put subjects in without a round trip — the browser suite's seam, and the one way to test a
 * renderer whose input is a fetch. Marks the read done, so nothing else goes out afterwards.
 */
export function __seed(map) {
  for (const [id, title] of Object.entries(map || {})) titles.set(String(id), String(title));
  loaded = true;
}

/**
 * Read every item's subject, once per session.
 *
 * `folded=1` so a row another report has TAKEN OVER still resolves: its id goes on being
 * NAMED in prose long after it leaves the open lists, and an unresolvable id is precisely the
 * failure this exists to end.
 */
export function loadRefTitles() {
  if (loaded || inflight) return inflight || Promise.resolve();
  inflight = api("/api/items?folded=1&limit=2000")
    .then((data) => {
      for (const row of (data && data.items) || []) {
        const id = String(row.id || "");
        const title = String(row.title || "").trim();
        if (id && title) titles.set(id, title);
      }
      loaded = true;
    })
    .catch(() => { /* a console that cannot read the index still renders every id as today */ })
    .finally(() => {
      inflight = null;
      for (const fn of waiting) { try { fn(); } catch { /* one bad subscriber, not the rest */ } }
      waiting.clear();
    });
  return inflight;
}
