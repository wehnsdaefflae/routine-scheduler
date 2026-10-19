// A BACKGROUND call's rows, and the kill confirmation that ends one (D118, D176) — the one
// place the console turns `engine/background.py`'s payloads into sentences.
//
// Its own module for two reasons. `transcript.js` is over the repo's 350-line house bar and
// frozen at its ratchet ceiling, so an addition belongs in a new file (CLAUDE.md Standards,
// tests/test_file_size.py) — and a backgrounded call has FOUR endings while every other kind has
// one, which is a responsibility rather than a special case:
//
//   started    ⏳ the turn came back at once; no exit code, no output, no verdict yet
//   landed     the real observation, rendered exactly as a synchronous one of its kind would be
//              (so it is NOT here — `transcript.js`'s per-kind branches own it)
//   unread     it landed after the last turn, or was still running at run end (`abandoned`):
//              work that happened and nothing acted on
//   cancelled  the run called `kill handle=…`; `background.cancel` records the call rather than
//              dropping it, so the transcript does not stop mid-sentence
//
// The vocabulary is the ENGINE's, deliberately — `cancelled` rather than `abandoned` (a run that
// chose to drop a result is not a run that ran out of turns), `had_landed` for a finished result
// thrown away, and `stopped_work` for the distinction `background.cancel` works hardest to keep:
// for `util`/`script`/`shell` the command's whole process group is terminated and the WORK stops,
// for `llm`/`decide`/the reads only the DELIVERY stops and the call runs to its own end unread.
// A surface folding those together cannot tell the operator which of them happened, and the
// cancelling run gets exactly ONE confirmation.

//: What a cancelled call's ending WAS, in one clause — the three the payload distinguishes.
function cancelEnding(o) {
  if (o.had_landed) return "it had already finished, so its result existed and was dropped";
  return o.stopped_work
    ? "its command's process group was terminated, so the work really stopped"
    : "only its delivery stopped; the work runs to its own end unread";
}

// The cancelled call's OWN observation row (engine/background.py `cancel`, the `observation`
// event it writes). Before F723 this payload matched no background branch and fell through to
// the ordinary util branch, where it rendered `undefined → exit undefined` — a row that existed,
// carried no state, and told the reader nothing true: not that the call was cancelled, not which
// handle it was, and not whether the work had really been stopped.
function cancelledText(o) {
  return `CANCELLED — ${o.kind} ${o.handle}: ${cancelEnding(o)}`
    + (o.note ? `\n${o.note}` : "")
    + (o.started_turn ? `\nstarted at turn ${o.started_turn}; its result was never delivered` : "");
}

/** The text of a background call's row, or null when `o` is not one of them.
 *
 * Returning null (rather than "") is what lets the caller keep its per-kind chain: an empty
 * string is a legitimate rendering, "this is not mine" is not.
 */
export function backgroundText(o) {
  if (!o || !o.background) return null;
  if (o.started) {
    // D118: the call is RUNNING. It has no exit code and no output yet, so it must be rendered
    // before the per-kind branches — the util branch would print "exit undefined", which is
    // exactly the misrender the secret-gate branch exists to prevent for the other
    // callable-with-no-exit case.
    return `⏳ ${o.kind} running in the background as ${o.handle} — ${o.brief || ""}\n`
      + "its result arrives as its own row at a later turn";
  }
  if (o.cancelled) return cancelledText(o);
  if (o.abandoned) {
    return `${o.kind} ${o.handle} was still running when the run ended — its result is lost`
      + (o.llm_calls_abandoned ? ` (${o.llm_calls_abandoned} model call(s) closed)` : "");
  }
  if (o.unread) {
    // A result that LANDED after the run's last turn. Seen in the render (2026-10-04) and fixed
    // there: with only a border colour to go on, this row's summary read
    // `result — websearch → exit 0 …`, character for character the ordinary success above it,
    // and its body `42 hits` said nothing about nobody having read them. A reader scanning
    // summaries — which is how a transcript is read — saw a clean result. The class assertion in
    // the browser test passed throughout, because the class was never what was wrong.
    return `NEVER READ BY THE RUN — ${o.kind} ${o.handle} finished after the last turn, so `
      + "nothing acted on this result:\n"
      + (o.name ? `${o.name} → exit ${o.exit}\n` : "")
      + (o.stdout || "") + (o.stderr ? `\n[stderr] ${o.stderr}` : "");
  }
  return null;   // a LANDED result: an ordinary observation of its kind, rendered as one
}

/** The row's state word for a background call, or "" when the row is not one.
 *
 *   running   — the turn came back at once and nothing is known about the outcome yet, which is
 *               a different thing from success and from failure.
 *   lost      — a result the run never read (`unread`, `abandoned`). The result may be right
 *               there in the row, and still nothing acted on it.
 *   cancelled — the run stopped this call on purpose. Not `lost`: nothing was mislaid and the
 *               ending is the one ending whose reason is known.
 */
export function backgroundState(o) {
  if (!o) return "";
  if (o.cancelled && (o.background || o.kind === "kill")) return "cancelled";
  if (!o.background) return "";
  if (o.started) return "running";
  if (o.unread || o.abandoned) return "lost";
  return "";
}

/** The `kill` action's confirmation sentence — ONE kill action, TWO namespaces.
 *
 * D176 option (a) kept them separate on purpose: `n` addresses a sub-workflow, the newer
 * `handle` addresses a background call. Before F723 this sentence read the NUMBER from both, so
 * `kill handle=bg1` rendered **"sub-workflow #undefined terminated"** — false twice over (it
 * named nothing, in the vocabulary of the other namespace) and the only confirmation a
 * cancelling run gets.
 */
export function killText(o) {
  if (o.error) return o.error;
  if (o.handle) {
    const what = o.background_kind
      ? `${o.background_kind}${o.brief ? `: ${o.brief}` : ""}` : "background call";
    const ending = o.had_landed
      ? "it had already finished, so its result was dropped"
      : o.stopped_work
        ? "the work is being stopped: its whole process group is terminated"
        : "the work is NOT stopped — only its delivery; it runs to its own end unread";
    return `background call ${o.handle} cancelled (${what}) — ${ending}`
      + `\nits observation will not be delivered; what it spent is booked against this run `
      + "either way."
      + (Array.isArray(o.in_flight)
        ? `\nstill in flight: ${o.in_flight.join(", ") || "nothing"}` : "");
  }
  return `sub-workflow #${o.n} ${o.already_finished ? "had already finished" : "terminated"}`;
}

/** The ACTION line's brief for a kill, or null to leave it to `BRIEF_FIELD`.
 *
 * `BRIEF_FIELD.kill` is `n` and must stay so: `transcript.js` holds that map in lockstep with
 * `engine/actionschema.py` (tests/test_actions.py parses the JS literal). So a handle-addressed
 * kill rendered a BLANK brief — the same defect as the sentence above, one line earlier in the
 * same row — and the fix belongs here rather than in the mirrored map.
 */
export function killBrief(a) {
  return a && a.kind === "kill" && a.handle ? `handle=${a.handle}` : null;
}
