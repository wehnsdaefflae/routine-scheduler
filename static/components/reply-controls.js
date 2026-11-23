// The controls a REPLY bubble carries. Their own module because components/chat.js sits over
// the repo's 350-line house bar and was frozen at its ratchet ceiling — the bar is per FILE, so
// a new module is the answer a new stylesheet is for an over-bar stylesheet (static/index.html
// says so at each `<link>`).
//
// Two families, deliberately unalike:
//   ⑂ branch           a LABELLED control in the reply's FOOTER row, always visible at reading
//                      contrast (D306 pt 1). It used to be a corner glyph at opacity 0 until
//                      the bubble was hovered, beside three others — and on a touch screen
//                      never visible at all, so the one move branches needed people to find
//                      was the one nobody could see. Branching was "unused only because the UI
//                      is bad" (operator, 2026-11-21 code review).
//   ⟲ rewind · ⚑ flag  quiet corner glyphs, untouched: they are remedies for a reply that went
//                      wrong, not invitations to take a line of work further.

import { el } from "/static/util.js";

// "Branch from here" (R1006): forking is conceptually "fork AT a turn" — and the only control
// for it lived in the conversation HEADER behind a free-text prompt asking the user to type a
// turn number — a translation from "this reply, here" into an integer they had to go and count.
// A REPLY is a clean turn boundary (its `turns` is the turn its finish action ran on, the exact
// number `cut_index_for_turn` snaps to), so the reply itself is the fork point and the number
// never has to be spoken. The LABEL is D306 pt 1: a glyph in a corner reads as decoration, and
// an offer nobody can see is not an offer.
export function branchFromHere(turn, onBranch) {
  if (!onBranch || !Number.isInteger(turn)) return null;
  return el("button", { class: "branch-from", "data-branch-turn": String(turn),
    title: `branch the conversation from here (turn ${turn}) — the new one inherits `
         + "everything up to and including this reply; this one is untouched",
    onclick: () => onBranch(turn) }, "⑂ branch");
}

// ⟲ rewind-from-here: the clicked reply's turn IS the cut point (R1006-style), so rewind gets a
// per-message control instead of only the run view's turn-prompt. Terminal-only; the onRewind
// wiring toasts if the conversation is still mid-reply.
export function rewindBtn(turn, onRewind) {
  if (!onRewind || !Number.isInteger(turn)) return null;
  return el("button", { class: "rewind-msg", "data-rewind-turn": String(turn),
    title: `rewind the conversation to here (turn ${turn}) — everything after this reply is `
         + "archived (reversible) and the conversation re-opens live from this point",
    onclick: () => onRewind(turn) }, "⟲");
}

// ⚑ flag-as-refusal (operator decision 2026-10-01): this reply is a refusal the conversation's
// uncensored model should answer instead — and go on answering. Every reply carries it, the
// engine's own failure replies included: a classifier refusal that exhausted the chain ends
// as one. The reply names itself by its turn AND its own ts, because such a reply ran no turn
// of its own and shares the turn number of the reply before it. The gate and the mid-reply
// toast are the onFlag wiring's (components/refusalflag.js).
export function flagBtn(ev, onFlag) {
  if (!onFlag || !Number.isInteger(ev.turns)) return null;
  return el("button", { class: "flag-msg", "data-flag-turn": String(ev.turns),
    title: "flag this reply as a refusal — it and every reply after it are discarded, your "
         + "message is re-sent, and the conversation's uncensored model answers it and "
         + "carries the conversation from here",
    onclick: () => onFlag({ turn: ev.turns, ts: ev.ts || "" }) }, "⚑");
}
