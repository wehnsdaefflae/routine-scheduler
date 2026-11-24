// The fleet's TOKEN PACE chip for the Routines page header: what every routine has spent in
// tokens this month, and where this month's rate lands by month end.
//
// Its own component because dashboard.js is at the house size bar, and because the reading is
// self-contained: one GET, one chip, no page state. It answers the operator's question — "so we
// can anticipate our token budget running out" — at the level the question lives at, since a
// subscription's window is spent by every routine at once and no single card can show that.
//
// It names NO ceiling, deliberately: per-routine token budgets are unset (-1 = unlimited) and the
// window that actually binds belongs to the model gateway, which owns the subscription logins.
// A rate and a destination let the reader compare with whatever ceiling they hold; a invented
// percentage would not (readmodels/token_pace.py says the same from the server's side).

import { api } from "/static/api.js";
import { el, fmtNum } from "/static/util.js";

/** The chip, hidden until the reading arrives (and left hidden when there is nothing to say). */
export function tokenPaceChip() {
  const chip = el("span", { class: "chip bare", hidden: true });
  api("/api/routines-token-pace").then(({ fleet }) => {
    if (!fleet?.tokens) return;          // a fresh instance with no usage stream yet
    // The projection is withheld early in the month (token_pace.MIN_ELAPSED_FRAC), so the chip
    // has two honest forms rather than one with a meaningless number in it.
    chip.textContent = fleet.projected_tokens
      ? `${fmtNum(fleet.tokens)} tok · ~${fmtNum(fleet.projected_tokens)} by month end`
      : `${fmtNum(fleet.tokens)} tok this month`;
    chip.title = "every routine's tokens this month, from the durable usage stream"
      + (fleet.projected_tokens
          ? " — and this month's rate carried to month end. A projection, not a ceiling: a "
            + "subscription's own window is on the model gateway's panel."
          : ". Too early in the month to project from.");
    chip.hidden = false;
  }).catch(() => {});                    // a chip is never worth a page error
  return chip;
}
