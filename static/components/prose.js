// CONSOLE PROSE: markdown, with every item id in it linked to its item.
//
// The operator, 2026-11-23: *"ids like D306, D307, D307b, or D310 in messages should all be
// hyperlinks to the actual item."* He said it about a run's own narration, where an id was
// plain text while the Messages page linked the same id — and the cause was structural:
// `linkifyRefs` was applied per surface, by twelve call sites, so every surface nobody thought
// of rendered bare ids. Fixing one more surface would have left the class in place.
//
// WHY THIS MODULE AND NOT `md.js`. Item references are a DOMAIN concept — they need the item
// index, which needs the fetch layer — and `md.js` is a pure text-to-DOM renderer that nearly
// every module imports. Putting the linkification there made `md.js` depend on `api.js`
// transitively, which is a cycle, which forced a dynamic import, which made the links arrive a
// microtask after the node did: a race that showed up as a flaky test and would have shown up
// as ids that link only sometimes. So the composition lives HERE, one layer up, where a
// component may know about both: `md.js` stays pure, `reflinks.js` stays the one owner of what
// a reference says, and this module is the single place that says console prose has links in it.
// (The operator's own words on the first attempt, which went looking for a file with room:
// *"you shouldn't just go hunting for files with slack. you need to maintain a reasonable
// organization of code parts in files"*.)
//
// A surface switches to linked prose by changing WHERE it imports `md`/`mdInline` from — the
// names and the call signatures are identical, so adopting it costs no line in a file that is
// at its size ceiling, and a surface that must stay unlinked keeps importing `md.js` directly.

import { linkifyKnownRefs } from "/static/components/reflinks.js";
import { md as mdOnly, mdInline as mdInlineOnly } from "/static/md.js";

/** A block of prose — a finish summary, a model's reply — with its item ids linked. */
export function md(text, cls = "md") {
  return linkifyKnownRefs(mdOnly(text, cls));
}

/** One line of prose — a run's narration, a question — with its item ids linked. */
export function mdInline(text) {
  return linkifyKnownRefs(mdInlineOnly(text));
}
