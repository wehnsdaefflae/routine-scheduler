// The rows of a message element that NAME a concept: a turn's side fields, and the strip that
// says a general rule FIRED. Both link the thing they name (F591 — operator, 2026-09-30: "also
// if reminders or rules fire it needs to be nicely highlighted, formatted, and link to the
// specific rule or reminder").
//
// Its own module for the reason obs-text.js, obs-attachments.js, obs-background.js and
// obs-accounting.js are theirs: static/components/transcript.js is over the repo's 350-line
// house bar and held at its ratchet ceiling, so an addition belongs in a new file (CLAUDE.md
// Standards, tests/test_file_size.py). These two pieces are a responsibility rather than a
// special case — they are the message element's CONCEPT rows, and they grow whenever a side
// field or a between-turn mechanism learns to name something that has a page of its own.
//
// The division of labour: this module builds DOM from one payload and nothing else — no
// closure, nothing about the live view, no page state. transcript.js keeps what needs the page
// (the turn box, the folds, the clocks) and decides WHERE a row goes.
//
// THE ONE RULE, inherited from conceptlinks.js: LINK FROM STRUCTURED FIELDS, NEVER FROM FREE
// TEXT. Every identifier below is read from a field the action or the observation already
// carries — `a.remind.id`, `a.remind_feedback.id`, `o.assists` — never by scanning prose. A rule
// slug like `work-order` would match ordinary English in every say line on the instance, which
// is the false-positive class reflinks.js warns about in its own header.

import { reminderLink, ruleLink } from "/static/components/conceptlinks.js";
import { mdInline } from "/static/md.js";
import { el } from "/static/util.js";

const REMIND_TITLE = "consequence reminder written this turn";
const FEEDBACK_TITLE = "how a hold turned out";

/** A reminder id, linked to the Recipe health row that holds its pattern and tally — or the
 *  plain code it already was when the mount does not know whose transcript this is. */
function rid(id, slug) {
  return id ? reminderLink(id, slug) : "?";
}

/** The `remind` side field: what this turn did to the routine's own reminders. */
function remindRow(r, slug) {
  if (r.op === "delete") {
    return el("div", { class: "note", title: REMIND_TITLE },
      "⚑ reminder ", rid(r.id, slug), " deleted");
  }
  const shared = r.scope === "global" ? " (shared — needs approval)" : "";
  // A revise names the reminder it rewrites; an `add` is named by the engine afterwards, so
  // there may be no id to link yet — the row says what was written either way.
  const head = r.op === "revise" ? ["revised ", rid(r.id, slug)]
    : r.id ? ["new reminder ", rid(r.id, slug)] : ["new reminder"];
  return el("div", { class: "note", title: REMIND_TITLE },
    "⚑ ", head, `${shared}: `,
    mdInline(`\`${r.regex || ""}\` → ${r.description || ""}`));
}

/** The `remind_feedback` side field: the label a run put on a hold it was given. */
function feedbackRow(f, slug) {
  return el("div", { class: "note", title: FEEDBACK_TITLE },
    "⚐ ", rid(f.id, slug), " ", mdInline(`→ **${f.label || "?"}**`));
}

/** Every side field riding this action, in the order they read — `note`, then what the turn did
 *  to a reminder, then how a hold turned out.
 *
 *  They ride ANY action exactly as `note` does and cost the same nothing, so they render the
 *  same way: without these rows the only trace of a run writing itself a caution, or labelling
 *  how one turned out, was inside the action json fold — which is where a reader goes to check
 *  a suspicion rather than to have one.
 *
 *  Returns an ARRAY (empty for an action carrying none), so the caller spreads it into its own
 *  `el(...)` child list and `el` drops what is not there. Nothing here is ever handed to
 *  `append` directly: a bare null renders the literal word "null" (tests/test_static_dom.py).
 */
export function sideFieldRows(a, slug) {
  const rows = [];
  if (a.note) {
    rows.push(el("div", { class: "note", title: "captured to state/notes.md" },
      "📌 ", mdInline(a.note)));
  }
  if (a.remind) rows.push(remindRow(a.remind, slug));
  if (a.remind_feedback) rows.push(feedbackRow(a.remind_feedback, slug));
  return rows;
}

/** One fired assist, as `<rule>/<id>`: the rule links to its prose, the declaration's own id
 *  rides along faintly so a rule with several declarations says WHICH one fired. */
function fired(key) {
  const [rule, ...rest] = String(key ?? "").split("/");
  const id = rest.join("/");
  return el("span", { class: "rule-fire-one" },
    ruleLink(rule), id ? el("span", { class: "faint" }, `/${id}`) : null);
}

/** The strip that says a general rule the routine practises fired on this row — or null.
 *
 *  A rule fire used to be indistinguishable from an ordinary observation: the row said HELD in
 *  prose inside a COLLAPSED fold and the rule slug was dead text in the middle of it. So this
 *  strip sits OUTSIDE the fold, where the reader sees that a rule fired without expanding
 *  anything, and it speaks the row-state vocabulary the 2026-09-28 request established
 *  (components/obs-text.js `obsState`): a `pre-action` assist HELD the action — the loudest row
 *  in the transcript, and the one nobody may scroll past — while every other moment fired on a
 *  result the run was getting anyway and changed nothing about it.
 *
 *  `o.assists` is the only field read, and the engine writes it on every event that carries an
 *  assist's line (engine/assist.py `hold` / `at_observation`, so a resumed leg can rebuild its
 *  fire guard from the transcript). A row whose payload names no rule gets NO strip: a row never
 *  states something its payload does not say.
 */
export function ruleFireStrip(o) {
  const keys = (o && Array.isArray(o.assists)) ? o.assists.filter(Boolean) : [];
  if (!keys.length) return null;
  const held = o.kind === "assist_hold";
  return el("div", {
    class: `rule-fire${held ? " held" : ""}`, "data-rule-fire": "",
    title: held
      ? "the action did NOT run — a general rule this routine practises governs this moment"
      : "a general rule this routine practises fired on this result",
  },
    "⦿ ",
    held ? "HELD by a rule this routine practises: " : "a rule this routine practises fired: ",
    ...keys.map((key, i) => [i ? ", " : "", fired(key)]));
}
