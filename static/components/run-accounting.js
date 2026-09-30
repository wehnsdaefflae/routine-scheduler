// The run page's GOAL rail: what THIS run reported at its end.
//
// A run accounts for every line it answers for, once, at its main finish (engine/accounting.py):
// each line of its recipe's `## Done when` — met, unmet, or not due — and each open outcome of
// the routine's finish line — the distance that remains, or met when the run proved it. A run
// started by hand with a BRIEF answers for that one line (`b1`) instead of the Done when. The
// accounting is a field of the run's status, not prose in its summary, so the rail reads it as
// data and labels each id with the line it stands for.
//
// The Done-when and finish-line labels are read from the routine NOW: a line the recipe has
// since renamed shows its id and the run's note, never a guessed text. The brief is the run's own.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

const ENTRY = /^\s*([dgb]\d+)\s+(met|unmet|not due|distance)\s*:\s*([\s\S]*)$/i;
const MARK = { met: "✓", unmet: "✗", "not due": "–", distance: "→" };
const WORD = { met: "met", unmet: "unmet", "not due": "not due", distance: "distance left" };
const CAPTION = { brief: "the brief", delivers: "what a finished run delivers",
                  "finish line": "finish line" };

/** `[{id, verdict, note}]` from the status field's `"d1 met: …"` strings; a line that does not
 *  parse is kept as it was written rather than dropped. */
export function parseAccounting(lines) {
  return (Array.isArray(lines) ? lines : []).map((raw) => {
    const m = ENTRY.exec(String(raw));
    return m ? { id: m[1].toLowerCase(), verdict: m[2].toLowerCase(), note: m[3].trim() }
             : { id: "", verdict: "", note: String(raw) };
  });
}

/**
 * createRunAccounting(mount, { slug }) → { set(detail) }
 * For a ROUTINE's run. `set` takes the run detail read (`accounting`, `brief`, `state`) and paints.
 */
export function createRunAccounting(mount, { slug }) {
  const body = el("div", { class: "acct", "data-run-accounting": "" });
  mount.append(body);
  let labels = null;                // id → {text, kind}
  const labelsFor = api(`/api/routines/${slug}/finish-line`).then((fl) => {
    labels = Object.fromEntries([
      ...(fl.done_when || []).map((d) => [d.id, { text: d.text, kind: "delivers" }]),
      ...(fl.outcomes || []).map((o) => [o.id, { text: o.text, kind: "finish line" }])]);
  }).catch(() => { labels = {}; });

  function paint(detail) {
    const rows = parseAccounting(detail?.accounting);
    if (!rows.length) {
      body.replaceChildren(el("div", { class: "faint small" },
        ["finished", "failed", "aborted"].includes(detail?.state)
          ? "this run reported no accounting — it answered for no brief, no Done when and no "
            + "open finish-line outcome"
          : "a run reports its accounting once, at its end"));
      return;
    }
    const groups = { brief: [], delivers: [], "finish line": [], other: [] };
    for (const r of rows) {
      const label = r.id === "b1" && detail?.brief ? { text: detail.brief, kind: "brief" }
        : labels?.[r.id];
      groups[label?.kind || "other"].push({ ...r, label });
    }
    const row = (r) => el("div", { class: `acct-row v-${(r.verdict || "raw").replace(" ", "-")}`,
      "data-acct": r.id || "raw" },
      el("span", { class: "acct-mark", title: WORD[r.verdict] || "" }, MARK[r.verdict] || "·"),
      el("div", { class: "acct-main" },
        el("div", { class: "acct-line" },
          r.id ? el("span", { class: "acct-id" }, r.id) : null,
          el("span", { class: "acct-text" }, r.label?.text || (r.id ? "a line no longer in the routine" : "")),
          r.verdict ? el("span", { class: "acct-verdict" }, WORD[r.verdict]) : null),
        r.note ? el("div", { class: "acct-note prose" }, r.note) : null));
    body.replaceChildren(...Object.entries(groups).flatMap(([kind, list]) => [
      list.length && CAPTION[kind] ? el("div", { class: "acct-cap" }, CAPTION[kind]) : null,
      ...list.map(row),
    ]).filter(Boolean));
  }

  return {
    async set(detail) {
      await labelsFor;
      paint(detail);
    },
  };
}
