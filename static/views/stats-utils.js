// The Stats tab's PER-UTIL table — every global util's life story: created / last revised
// (library git history), how often executed / successful / mis-called / permission-blocked,
// first & last execution (durable usage stream + a transcript backfill for pre-stream
// history). Honest about unknowns: no date = no git history, dashes = never seen;
// rejected/denied counting starts with the stream.
//
// Its own module because stats.js is at its size ceiling and this is one self-contained
// question ("which util is unreliable, and what would isolating it cost?") rather than a
// part of the tab's own composition.

import { el, fmtInt } from "/static/util.js";
import { foldRows, statSection } from "/static/views/stats-parts.js";

// D170 phase 2 — the isolation profile a util's calls would select, as a chip. ONE profile
// is derivable from a util header (`untrusted-input`: the call opens the network and carries
// no credential, so there is no counterparty the operator chose); the other authorized
// profile, `model-authored-code`, is a property of the write_util selftest CALL SITE and has
// no per-util row to sit in. Only the selected utils are marked: 95% of the stream composes
// exactly as it does today and a chip on every row would say nothing.
const profileChip = (r) => (r.isolation_profile
  ? el("span", { class: "chip", style: "margin-left:6px",
                 title: "opens the network and declares no credential — the profile the "
                      + "operator authorized a micro-VM for (D170 phase 2)" },
       r.isolation_profile)
  : "");

// The caption's cost line. The ruling that authorized the selector bounds it by a RATE —
// "affordable for a few calls per run and is not for all of them" — so the reading is calls
// PER RUN against phase 1's measured +392 ms and 179 MB per call. A share of all calls would
// not answer it: what a run waits for is its own calls, not the fleet's.
function isolationLine(u) {
  const iso = u?.isolation;
  if (!iso || !iso.utils) return null;
  const runs = (u.stream_records || 0) + (u.backfill_runs || 0);
  const rate = runs ? (iso.calls / runs) : null;
  const perRun = rate === null
    ? "no runs counted yet, so there is no rate to report"
    : `${rate.toFixed(1)} calls per run — about `
      + `${((rate * iso.added_ms_per_call) / 1000).toFixed(1)} s of added latency`;
  return [`Isolation: ${iso.utils} utils carry the ${iso.profile} profile, `,
          `${fmtInt(iso.calls)} calls over ${fmtInt(runs)} counted runs — ${perRun}, `,
          `at ${iso.added_ms_per_call} ms and ${iso.held_mb_per_call} MB per call (measured). `,
          "Everything else composes as it does today: the Landlock jail."];
}

export function utilsSection(u) {
  const rows = u?.utils || [];
  if (!rows.length) return null;
  const day = (iso) => (iso ? String(iso).slice(0, 10) : "—");
  const num = (n) => (n ? fmtInt(n) : "—");
  const head = el("tr", {},
    el("th", {}, "util"),
    el("th", {}, "created"),
    el("th", {}, "last revised"),
    el("th", { class: "num" }, "executed"),
    el("th", { class: "num", title: "exit 0" }, "ok"),
    el("th", { class: "num", title: "non-zero exit (util ran and failed)" }, "errors"),
    el("th", { class: "num", title: "exit 2 — called with bad arguments (argparse convention)" }, "syntax err"),
    el("th", { class: "num", title: "reserved util switched off / kind not permitted — rejected before running" }, "denied"),
    el("th", { class: "num", title: "malformed action (schema/field problems) — rejected before running" }, "rejected"),
    el("th", { class: "num", title: "called by a name that doesn't exist in the library" }, "missing"),
    el("th", {}, "first executed"),
    el("th", {}, "last executed"));
  const rowFor = (r) => {
    const okPct = r.executed ? ` (${Math.round((r.ok / r.executed) * 100)}%)` : "";
    return el("tr", {},
      // DELETED is a historical fact about a util, not a failure of one: 86 of the fleet's 196
      // rows carried it, which is 86 err-toned chips at rest on one page. Colour is state.
      el("td", { title: r.summary || "" }, r.name,
        r.in_library ? "" : el("span", { class: "chip disabled", style: "margin-left:6px" }, "deleted"),
        profileChip(r)),
      el("td", { class: "muted" }, day(r.created)),
      el("td", { class: "muted" }, day(r.revised)),
      el("td", { class: "num" }, r.executed ? fmtInt(r.executed) : "never"),
      el("td", { class: "num" }, r.executed ? fmtInt(r.ok) + okPct : "—"),
      el("td", { class: "num" }, num(r.error)),
      el("td", { class: "num" }, num(r.usage_error)),
      el("td", { class: "num" }, num(r.denied)),
      el("td", { class: "num" }, num(r.rejected)),
      el("td", { class: "num" }, num(r.missing)),
      el("td", { class: "muted" }, day(r.first_executed)),
      el("td", { class: "muted" }, day(r.last_executed)));
  };
  // Busiest first, and the utils the library no longer holds behind their own reveal — they are
  // history, and the question this table answers ("which util is unreliable?") is about the
  // ones that still exist.
  const live = rows.filter((r) => r.in_library).sort((a, b) => (b.executed || 0) - (a.executed || 0));
  const gone = rows.filter((r) => !r.in_library).sort((a, b) => (b.executed || 0) - (a.executed || 0));
  const body = [...foldRows(live.map(rowFor), 25, "utils"),
                ...foldRows(gone.map(rowFor), 0, "deleted utils")];
  const iso = isolationLine(u);
  return statSection("Global utils",
    ["per-util reliability: dates from the library's git history; counts from each run's usage record ",
     `(durable) plus ${fmtInt(u.backfill_runs || 0)} pre-stream runs backfilled from retained transcripts. `,
     "Denied/rejected calls never ran (caught at validation), so those counts begin with the stream.",
     ...(iso ? [el("br"), ...iso] : [])],
    head, body);
}
