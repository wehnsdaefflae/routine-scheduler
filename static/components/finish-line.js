// A routine's GOAL, in two halves with two owners.
//
// The FINISH LINE is the operator's: when this routine is done for good. Zero or more OUTCOMES,
// all of which must be reached, plus an optional date after which scheduling stops either way.
// Every outcome names its JUDGE, because three different parties decide three kinds of "done":
// the calendar (a date), the run (it proves it; the claim is checked against its own
// transcript), or the operator (only a person can say). It is a setting like any other on the
// page: edited in the draft, saved by the one accept.
//
// WHAT A FINISHED RUN DELIVERS is the recipe's `## Done when` — one line per outcome of ONE run,
// written where the design of a run lives and changed through the recipe (Revise recipe). Shown
// read-only, beside the verdict each of the last runs gave it, because those verdicts are what
// tell a person whether the recipe does what it says.

import { el, fmtAbs, when } from "/static/util.js";

const JUDGE_OPTIONS = [["run", "the run proves it"], ["you", "you decide"], ["date", "on its date"]];
const MAX_OUTCOMES = 12;                  // engine/finishline.problems — a readable finish line
const EMPTY = "This routine runs until you switch it off. Add a finish line if its job ends: a "
  + "date, an outcome the run can prove, or an outcome only you can judge. When it is reached, "
  + "scheduling stops and a Decisions card asks you to confirm retiring it.";

// The LOCAL calendar day. The scheduler judges a date outcome by its own local day
// (engine/finishline.today), and the console is read in the server's zone; the UTC day this
// used matched it in no zone but UTC, so for the hours between the two midnights the editor
// said "reached" (or not) against the scheduler's verdict.
const today = () => fmtAbs(new Date()).slice(0, 10);

function reachedWhen(o) {
  if (o.judge === "date") return o.date ? `${o.date} arrives` : "its date arrives (set one)";
  if (o.judge === "run") return "a run proves it — the claim is checked against that run's own transcript";
  return "you judge it reached";
}

/**
 * finishLineEditor({ value, info, set }) → node
 * `value` is the draft `finish_line` ({outcomes, until}); `info` the GET …/finish-line read,
 * whose outcomes carry what the RUNS reported (distance, evidence) — joined here by id.
 */
export function finishLineEditor({ value, info, set }) {
  const doc = { outcomes: (value?.outcomes || []).map((o) => ({ ...o })), until: value?.until || "" };
  const reported = new Map((info?.outcomes || []).filter((o) => o.id).map((o) => [o.id, o]));
  const host = el("div", { class: "fl", "data-finish-line": "" });
  const commit = () => set(structuredClone(doc));

  function add(judge) {
    doc.outcomes.push({ id: "", text: "", judge, date: "", status: "open" });
    render();
    host.querySelectorAll(".fl-text")[doc.outcomes.length - 1]?.focus();
  }

  function consequence(o) {
    const several = doc.outcomes.length > 1;
    return el("div", { class: "fl-then" },
      el("b", {}, "Reached when: "), reachedWhen(o), ". ",
      el("b", {}, "Then: "), several
        ? "once every outcome here is reached, scheduling stops and you get a card. "
        : "scheduling stops and you get a card. ",
      el("b", {}, "Until then: "), o.judge === "date"
        ? "the calendar decides; no run reports on it."
        : "every run reports the remaining distance here.");
  }

  function statusCell(o) {
    if (o.judge === "date") {
      return o.date && o.date <= today()
        ? el("span", { class: "fl-reached" }, "reached") : null;
    }
    if (o.status === "met") {
      return el("span", { class: "fl-status" },
        el("span", { class: "fl-reached" },
          o.judge === "you" ? "you judged it reached" : "reached"),
        el("button", { type: "button", class: "btn small ghost", "data-reopen": "",
          title: "not reached after all — every run reports on it again",
          onclick: () => { o.status = "open"; render(); commit(); } }, "reopen"));
    }
    if (o.judge === "you") {
      return el("button", { type: "button", class: "btn small", "data-judge-reached": "",
        title: "you are the judge of this one — marking it reached takes effect when you accept",
        onclick: () => { o.status = "met"; render(); commit(); } }, "I judge this reached");
    }
    return null;
  }

  function report(o) {
    const r = reported.get(o.id);
    if (!r) return null;
    if (o.status === "met" && r.met_run) {
      return el("div", { class: "fl-report" }, "proved by ",
        el("a", { href: `#/run/${r.met_run}` }, "this run"), r.met_ts ? " · " : "",
        r.met_ts ? when(r.met_ts, { mode: "rel" }) : null,
        r.evidence ? el("span", { class: "prose" }, ` — ${r.evidence}`) : null,
        r.disputed ? el("span", { class: "fl-disputed", title: r.disputed },
          " · a check of that run's transcript disagreed") : null);
    }
    if (o.status !== "met" && r.distance) {
      return el("div", { class: "fl-report", "data-distance": o.id }, "latest distance · ",
        r.distance_run ? el("a", { href: `#/run/${r.distance_run}` }, "run") : "run",
        r.distance_ts ? " " : "", r.distance_ts ? when(r.distance_ts, { mode: "rel" }) : null,
        el("span", { class: "prose" }, ` — ${r.distance}`));
    }
    return null;
  }

  function row(o, i) {
    const text = el("input", { type: "text", class: "fl-text", value: o.text,
      placeholder: o.judge === "date" ? "what the date stands for, e.g. the submission deadline"
        : "what is true when this is done", "data-nopersist": true,
      oninput: () => { o.text = text.value; commit(); } });
    const judge = el("select", { class: "fl-judge", title: "who decides that this is reached",
      "data-nopersist": true,
      onchange: () => {
        o.judge = judge.value;
        if (o.judge !== "date") o.date = "";
        if (o.judge === "date") o.status = "open";
        render();
        commit();
      } }, ...JUDGE_OPTIONS.map(([v, label]) => el("option", { value: v }, label)));
    judge.value = o.judge;
    const date = o.judge === "date"
      ? el("input", { type: "date", class: "fl-date", value: o.date || "", "data-nopersist": true,
          onchange: () => { o.date = date.value; render(); commit(); } })
      : null;
    return el("div", { class: `fl-row judge-${o.judge}${o.status === "met" ? " met" : ""}`,
      "data-outcome": o.id || `new-${i}` },
      el("div", { class: "fl-line" }, text, judge, date, statusCell(o),
        el("button", { type: "button", class: "btn small ghost fl-remove", title: "remove this outcome",
          onclick: () => { doc.outcomes.splice(i, 1); render(); commit(); } }, "remove")),
      o.judge === "date" && !o.date
        ? el("div", { class: "fl-problem" }, "a date outcome needs its date") : null,
      report(o),
      consequence(o));
  }

  function untilRow() {
    const input = el("input", { type: "date", class: "fl-date", value: doc.until, "data-until": "",
      "data-nopersist": true, onchange: () => { doc.until = input.value; render(); commit(); } });
    return el("div", { class: "fl-until" },
      el("label", { class: "row", style: "gap:8px" },
        el("span", {}, "Stop scheduling after"), input,
        doc.until ? el("button", { type: "button", class: "btn small ghost",
          onclick: () => { doc.until = ""; render(); commit(); } }, "clear") : null),
      el("div", { class: "fl-then" },
        el("b", {}, "Reached when: "), doc.until ? `the day after ${doc.until} begins` : "a date is set here",
        ". ", el("b", {}, "Then: "), "scheduling stops whatever the outcomes say and you get a card. ",
        el("b", {}, "Until then: "), "the outcomes above decide."));
  }

  function render() {
    const full = doc.outcomes.length >= MAX_OUTCOMES;
    const adders = el("div", { class: "row fl-add", style: "gap:8px" },
      el("button", { type: "button", class: "btn small", disabled: full ? "" : null,
        "data-add-outcome": "run", onclick: () => add("run") }, "+ an outcome the run proves"),
      el("button", { type: "button", class: "btn small", disabled: full ? "" : null,
        "data-add-outcome": "you", onclick: () => add("you") }, "+ an outcome you judge"),
      el("button", { type: "button", class: "btn small", disabled: full ? "" : null,
        "data-add-outcome": "date", onclick: () => add("date") }, "+ a date"));
    const reached = info?.reached
      ? el("div", { class: "fl-done", "data-reached": "" }, `Reached: ${info.reached}. `
          + "Scheduling has stopped; the Decisions page asks you to confirm retiring it.")
      : null;
    host.replaceChildren(...[
      reached,
      doc.outcomes.length || doc.until ? null : el("div", { class: "fl-empty prose" }, EMPTY),
      doc.outcomes.length ? el("div", { class: "fl-rows" }, ...doc.outcomes.map(row)) : null,
      doc.outcomes.length > 1 ? el("div", { class: "faint small" },
        "Every outcome must be reached — the finish line is ALL of them.") : null,
      adders,
      untilRow(),
    ].filter(Boolean));
  }
  render();
  return host;
}

// ---- what a finished run delivers ---------------------------------------------------------

const MARK = { met: "✓", unmet: "✗", "not due": "–" };

/**
 * doneWhenPanel({ slug, info, reviseHref }) → node
 * `info` is the GET …/finish-line read: `done_when` lines and the `verdicts` of the last runs
 * (newest first). The strip reads oldest → newest, left to right, like every run strip here.
 */
export function doneWhenPanel({ slug, info, reviseHref, onJump }) {
  const lines = info?.done_when || [];
  const runs = [...(info?.verdicts || [])].reverse();
  const cell = (line, v) => {
    const got = v.accounting?.[line.id];
    const verdict = got ? got[0] : "";
    const note = got ? got[1] : "";
    return el("a", { class: `dw-cell ${verdict ? verdict.replace(" ", "-") : "none"}`,
      href: `#/run/${slug}:${v.run}`,
      title: `run ${v.run} · ${verdict || "not accounted"}${note ? ` — ${note}` : ""}` },
      MARK[verdict] || "·");
  };
  const rows = lines.map((line) => el("div", { class: "dw-row", "data-done": line.id },
    el("span", { class: "dw-id" }, line.id),
    el("div", { class: "dw-main" },
      el("div", { class: "dw-text prose" }, line.text),
      el("div", { class: "dw-stage" }, line.stage ? `produced by stage ${line.stage}`
                                                : "produced by no single stage")),
    runs.length
      ? el("div", { class: "dw-strip", title: "the last runs' verdicts, oldest → newest" },
          ...runs.map((v) => cell(line, v)))
      : el("span", { class: "faint small" }, "no run has reported yet")));
  const jump = (id, text) => el("a", { href: "#", "data-jump": id,
    onclick: (e) => { e.preventDefault(); onJump?.(id); } }, text);
  return el("div", { class: "dw", "data-done-when": "" },
    lines.length ? el("div", { class: "dw-rows" }, ...rows)
      : el("div", { class: "muted small" },
          "This recipe has no ", el("code", {}, "## Done when"), " section, so a run accounts for "
          + "nothing at its end. One line per outcome of one run — ",
          el("code", {}, "- d1 · <stage> — <outcome>"), " — added through Revise recipe."),
    lines.length && runs.length ? el("div", { class: "dw-legend faint small" },
      "✓ met · ✗ unmet · – not due · each mark opens its run; an unmet mark shows what remained "
      + "when you hover it") : null,
    el("p", { class: "set-desc muted small" },
      "Every run reports each line once, at its end. A 'met' claim is checked against that run's "
      + "own transcript. An unmet line hands its remaining work to the next run. Nothing here "
      + "limits how much a run does."),
    el("div", { class: "row", style: "gap:10px" },
      reviseHref
        ? el("a", { class: "btn small", href: reviseHref, "data-revise-recipe": "" },
            "Change through Revise recipe")
        : el("button", { type: "button", class: "btn small", disabled: "",
            title: "Revise recipe works from a finished run — run the routine once first" },
            "Change through Revise recipe"),
      el("span", { class: "faint small" }, "or edit ",
        jump("sec-recipe", "main.md"), " directly")),
    el("div", { class: "dw-pointer small" },
      "Things a run must never do live in ",
      jump("sec-permissions", "Permissions"), ", ",
      jump("sec-general-rules", "General rules"), " and ",
      jump("sec-shared-reminders", "Reminders"), "."));
}
