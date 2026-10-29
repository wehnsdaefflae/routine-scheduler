// What an OBSERVATION row says, and what it means — one sentence per action kind, and the one
// word that colours the row. The console's whole vocabulary for "what came back".
//
// Its own module for the reason obs-background.js and obs-accounting.js are theirs:
// `static/components/transcript.js` is over the repo's 350-line house bar and frozen at its
// ratchet ceiling, so an addition belongs in a new file (CLAUDE.md Standards,
// tests/test_file_size.py) — and the per-kind chain is a responsibility rather than a special
// case. It grows by one branch every time the engine learns an action kind or an action learns
// an ending, which is the growth the ratchet exists to redirect: a branch added HERE costs
// transcript.js nothing.
//
// The division of labour with transcript.js: this module is PURE — payload in, string out, no
// DOM, no closure, nothing about the live view. transcript.js keeps everything that needs the
// page (the collapsing <details>, the thumbnails, the clocks) and everything a source-level
// guard pins to that file by name (`BRIEF_FIELD`, which tests/test_actions.py holds in lockstep
// with engine/actionschema.py, and the `SIMPLE` renderer table, which
// tests/test_static_transcript.py reads to prove every EVENT_TYPES member has a renderer).
//
// The ONE rule for everything in here: a row never states something the payload does not say.
// Every branch below exists because a reader was once told something false — an exit code for a
// call that never ran, a span for a payload carrying no numbers, "sub-workflow #undefined" for a
// handle-addressed kill, one question where a batch filed several.

import { accountingGaps } from "/static/components/obs-accounting.js";
import { backgroundState, backgroundText, killText } from "/static/components/obs-background.js";
import { compressionInfo, fullOutput } from "/static/util.js";

// What an observation MEANS, as one word, for the row's appearance. Operator, 2026-09-28:
// "different util exit codes should change the color of the response in the ui. also if tool
// calls are held because of a reminder, this also needs some visual sprucing up."
//
// The vocabulary is by MEANING, not by exit code, and the distinction is the whole point:
//   held    — the action did NOT run and is waiting for the run to decide again. A decision
//             point rendered as history, and the one row a reader must not scroll past.
//   refused — the engine declined it (no such util, a declined approval, a rejected finish).
//   usage   — exit 2: the CALL was malformed. The util never did its job and the repair is in
//             the arguments. 3.8% of fleet util calls, and the reason `usage` is its own state.
//   timeout — exit 124: the deadline killed it. Nothing is known about what it would have
//             done, which is different from knowing it failed.
//   error   — it ran and reported failure.
//   ok      — it ran and reported success.
//
// A NONZERO EXIT IS NOT AUTOMATICALLY AN ERROR, and this is the F506/F523 lesson twice
// learned: `grep -q`, `test -f` and `diff` all answer with exit 1, and a shell action
// documents a nonzero exit as often being the answer. So `shell` gets no error state from its
// code alone — only the two codes that mean something specific.
const TIMEOUT_EXIT = 124, USAGE_EXIT = 2;

/** The row's one-word state, or "" for a row that is merely a result. */
export function obsState(o) {
  if (!o) return "";
  //   running / lost / cancelled — the three states only a BACKGROUND call can be in, worded
  //   and decided by components/obs-background.js beside the rows themselves (D118/D176), so
  //   the state and the sentence can never disagree about what the row is.
  const bg = backgroundState(o);
  if (bg) return bg;
  if (o.kind === "reminder_hold" || o.kind === "assist_hold") return "held";
  if (o.missing || o.declined || o.rejected || o.callers) return "refused";
  // A write or edit that carried a script (engine/thenscript.py) LANDED — so what the row
  // means is what the script said about it. A change that did not land carries an error
  // and no script, and reads as the error below.
  if (o.then_script) return obsState(o.then_script);
  if (o.kind === "util" || o.kind === "script" || o.kind === "shell") {
    if (o.exit == null) return "";
    if (o.exit === TIMEOUT_EXIT) return "timeout";
    if (o.exit === USAGE_EXIT) return "usage";
    if (o.exit === 0) return "ok";
    // A nonzero that is neither: an error for the two kinds whose contract says so, and
    // merely a result for `shell`, whose own documentation says a nonzero is often the answer.
    return o.kind === "shell" ? "" : "error";
  }
  if (o.selftest_ok === false || o.lint_ok === false || o.error || o.edit_failed) return "error";
  return "";
}

// The script a write or edit carried (`then_script`, engine/thenscript.py), worded as the
// script's own row would be, under the change's line — or why it never ran.
function thenScriptText(o) {
  if (o.then_script_skipped) return `\n[then_script NOT run] ${o.then_script_skipped}`;
  const s = o.then_script;
  if (!s) return "";
  if (s.pending_secrets || s.declined_secrets) {
    return `\nthen_script ${s.name} NOT run — secret exposure `
      + (s.declined_secrets ? "declined" : `pending for ${s.pending_secrets.join(", ")}`);
  }
  if (s.missing) return `\nthen_script: script "${s.name}" does not exist`;
  if (s.exit == null) return `\nthen_script ${s.name} NOT run: ${s.error || ""}`;
  return `\nthen_script ${s.name} → exit ${s.exit}\n${s.stdout || ""}`
    + (s.stderr ? `\n[stderr] ${s.stderr}` : "") + fullOutput(s.full_output);
}

/** Everything an observation row SAYS, as one string — the per-kind chain.
 *
 * Pure: the payload in, the row's words out. The caller owns the page (the collapsing
 * <details>, a `view_image`'s thumbnails) and asks `obsState` separately for the word that
 * colours it.
 *
 * The LAST branch is `JSON.stringify`, and it is a fallback rather than a default: every kind
 * that reaches it shows the reader raw payload. That is how `view_image` once rendered (F559)
 * and how a held action once rendered — so a new kind belongs in a branch of its own, here.
 */
export function observationText(o) {
  let text;
  // Every ending a BACKGROUND call can have — started, cancelled, unread, abandoned — is
  // worded by components/obs-background.js, which owns that vocabulary (D118/D176). It must
  // come before the per-kind branches: none of those rows has an exit code, and the util
  // branch below would print "exit undefined" — the misrender the secret-gate branch exists
  // to prevent for the other callable-with-no-exit case. A LANDED background result returns
  // null and falls through, rendered exactly as a synchronous call of its kind would be.
  const bgText = backgroundText(o);
  if (bgText !== null) {
    text = bgText;
  } else if ((o.kind === "util" || o.kind === "script") && (o.pending_secrets || o.declined_secrets)) {
    // The secret gate stopped the call before it ran — no exit code exists, and rendering one
    // read as "exit undefined". Names only for a PENDING request (the run's own ask); a
    // declined one is counted, as the model is told (observations._secret_gate).
    const declined = o.declined_secrets || [];
    text = `${o.name} NOT run — secret exposure `
      + (declined.length ? `declined for ${declined.length} secret${declined.length === 1 ? "" : "s"}`
        : `pending for ${o.pending_secrets.join(", ")}`)
      + (o.dialog ? `\nthe user replied without deciding: ${o.user_message}` : "");
  } else if (o.kind === "util" || (o.kind === "script" && o.exit != null)) {
    text = o.missing ? `util "${o.target || o.name}" does not exist (available: ${(o.available || []).join(", ")})`
      : o.listing != null ? `util catalog\n${o.listing}`
      : o.source != null ? `source of "${o.target}"\n${o.source}`
      : `${o.name} → exit ${o.exit}\n${o.stdout || ""}${o.stderr ? `\n[stderr] ${o.stderr}` : ""}`
        + fullOutput(o.full_output) + compressionInfo(o.compression)
        + (o.usage ? `\n[usage] ${o.usage}` : "") + (o.hint ? `\n[hint] ${o.hint}` : "");
  } else if (o.kind === "shell") {
    text = `exit ${o.exit}${o.cwd ? ` (in ${o.cwd})` : ""}\n${o.stdout || ""}`
      + (o.stderr ? `\n[stderr] ${o.stderr}` : "") + fullOutput(o.full_output) + compressionInfo(o.compression);
  } else if (o.kind === "write_util") {
    text = o.pending_approval ? `write_util "${o.name}": awaiting user approval`
      : o.declined ? `write_util "${o.name}": declined`
      : o.selftest_ok ? `write_util "${o.name}": selftest passed, committed`
      : `write_util "${o.name}": selftest FAILED\n${o.output || ""}`;
  } else if (o.kind === "read_file") {
    text = o.files  // batched multi-path read: one section per file
      ? o.files.map((f) => f.error ? `--- ${f.path} FAILED: ${f.error}`
                                   : `--- ${f.path} (${f.directory ? "directory listing, entries" : "lines"} ${f.start_line}-${f.end_line} of ${f.total_lines}) ---\n${f.content}`)
          .join("\n\n")
      : o.error || o.content || "";
  } else if (o.kind === "llm") {
    text = o.error || o.reply || "";
  } else if (o.kind === "write_file") {
    text = (o.error || `wrote ${o.bytes} bytes → ${o.path}`) + thenScriptText(o);
  } else if (o.kind === "edit_file") {
    text = (o.error || `replaced ${o.replacements} occurrence(s) in ${o.path}`)
      + thenScriptText(o);
  } else if (o.kind === "memory_read") {
    text = o.missing ? `no note "${o.name}" (topics: ${(o.topics || []).join(", ") || "none yet"})`
      : o.content || "";
  } else if (o.kind === "memory_write") {
    text = o.deleted ? `note "${o.name}.md" ${o.existed ? "deleted, INDEX updated" : "did not exist"}`
      : `note "${o.name}.md" ${o.created ? "created" : "revised"} (${o.lines} lines), INDEX updated`;
  } else if (o.kind === "spawn") {
    text = o.rejected ? `spawn REJECTED: ${o.reason}` :
      `sub-workflow #${o.n} "${o.label}" started (${o.workflow}) — running in parallel`;
  } else if (o.kind === "subruns") {
    text = (o.rows || []).map((r) =>
      `#${r.n} "${r.label}" [${r.workflow}] ${r.state} · ${r.turns} turns · ${r.elapsed_s}s`)
      .join("\n") || "no sub-workflows";
  } else if (o.kind === "kill") {
    // Two namespaces, one action (D176 option (a)): `n` a sub-workflow, `handle` a background
    // call. Reading the number from both is what made `kill handle=bg1` render
    // "sub-workflow #undefined terminated" — see components/obs-background.js killText.
    text = killText(o);
  } else if (o.kind === "wait") {
    text = o.error || ((o.finished || []).map((f) =>
      `#${f.n} "${f.label}" finished (${f.status}, ${f.turns} turns):\n${f.summary}`)
      .join("\n\n") || (o.timed_out ? "wait timed out" : "nothing new finished"));
  } else if (o.kind === "ask_user") {
    // FOUR endings, and the BATCH one is the row this branch exists for (D212).
    //
    // A batch is N RECORDS, not one question: `questions: [...]` on one action files one
    // decision card per entry, all sharing a batch id (engine/askbatch.py). So the summary
    // counts them — "3 questions filed as one batch (1 blocking)" — and must never read
    // "filed as deferred", the singular line, which would tell the operator ONE card is
    // waiting on the Decisions page while three are (tests/test_ask_batch.py pins exactly
    // that distinction for the engine's own admin line).
    //
    // Where the three fields come from, all of them the engine's `ask_user` batch
    // observation (engine/askbatch.py `handle`, its return value — this console invents
    // none of them):
    //   o.batch     the batch id every one of the filed records carries (`qb-<run_ts>-<turn>`),
    //               and the flag that says this row IS a batch;
    //   o.count     how many records were filed — `len(filed)`, i.e. one per question;
    //   o.blocking  how many of them are classed BLOCKING (the subject's own default: a typed
    //               access `request` blocks, a plain question closes, `blocks_work: true`
    //               overrides per question). Zero is the common case, hence the `|| 0`.
    //
    // `o.blocking` is NOT `mode`, and the two must not be conflated in the words. A batch is
    // always `mode: "deferred"` — `mode: "blocking"` is refused on a batch by the grammar,
    // because a run's process may not sit out an ask timeout for a sitting the operator
    // answers hours later. "blocking" here is the CLASS of a filed question (the work cannot
    // proceed without that answer), which suspends nothing.
    text = o.dialog ? `dialog reply (question stays open): ${o.user_message}`
      : o.answered ? `answered: ${o.answer}` : o.batch ? `${o.count} questions filed as one batch (${o.blocking || 0} blocking)`
      : o.timed_out ? "timed out → deferred" : "filed as deferred";
  } else if (o.kind === "reminder_hold") {
    text = `HELD — "${o.action}" did NOT run. It matches a consequence reminder:\n`
      + (o.reminders || []).map((r) => `· [${r.id} · ${r.scope}] ${r.description}`).join("\n")
      + "\nEmitting the same action again runs it (one hold per action string per run — per "
      + "reply, in a conversation).";
  } else if (o.kind === "assist_hold") {
    text = `HELD — "${o.action}" did NOT run. A general rule this routine practises `
      + `governs this moment:\n`
      + (o.lines || []).map((l) => `· ${l}`).join("\n")
      + "\nEmitting the same action again runs it (one hold per action string per run — per "
      + "reply, in a conversation).";
  } else if (o.kind === "finish" && o.rejected) {
    // SIX rungs reach this observation and each hands the turn back for its own reason, so
    // each is named — a run deferred for its accounting is not a hallucinated completion,
    // which is the opposite diagnosis.
    text = o.pending_user_input
        ? "finish DEFERRED — a user message arrived mid-finish; it is delivered instead"
      : o.accounting
        ? `finish DEFERRED — the accounting is incomplete: ${accountingGaps(o.accounting)}`
      : o.assist
        ? "finish DEFERRED — a general rule this routine practises governs the ending itself"
      : o.unbacked_claims
        ? `finish REJECTED — the summary claims ${o.unbacked_claims.join(", ")}, `
          + "which this run never did"
      : o.claims_unsupported
        ? "finish DEFERRED — the run's own transcript does not support the \"met\" claim on "
          + o.claims_unsupported.join(", ")
      : "finish REJECTED — no action had been executed yet (fabrication guard)";
  } else {
    text = JSON.stringify(o, null, 1);
  }
  // F559 (operator 2026-10-01: "i still want images that the system looks at previewed in the
  // message as thumbnail"). view_image had no case above, so it fell through to the JSON dump
  // and the person reading the transcript saw a path where the MODEL was shown the picture.
  // The WORDS are here; the thumbnails are the caller's, because they need the page.
  if (o.kind === "view_image") {
    const files = o.files || [];
    // `text` is the vision util's description where a text-only model got one — that IS what
    // the run reasoned about, so it is kept; an unreadable file reports its error instead.
    text = files.map((f) => f.error ? `${f.path}: ${f.error}`
      : f.text ? `${f.path}:\n${f.text}` : f.path).join("\n\n") || "no image";
  }
  return text;
}
