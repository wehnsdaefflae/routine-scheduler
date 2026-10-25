// The MOMENT TEST on a change (readmodels/change_frictions.py), beside its rate verdict: did
// the failures that repeated before it come back after it? One compact cell for the change's
// row, and one table for its opened detail.
//
// Colour is state, on the tokens base.css has: a repeated failure the change removed is OK, a
// failure it brought in is the failure tone, an aim the runs before it never repeated is the
// warning one, and what merely persists is quiet. `confounded` — the window had to reach across
// a neighbouring change — is a warning mark too, because then the reading belongs to those
// changes together. Every mark carries its word in a tooltip, never colour alone.

import { el } from "/static/util.js";

const AIM = {
  fixed: ["mo-fixed", "✓", "the change named this failure, it repeated before and is gone after"],
  persists: ["mo-persists", "·", "the change named this failure and it still happens"],
  unfounded: ["mo-unfounded", "?", "the change named a failure the runs before it did not repeat"],
  measuring: ["mo-quiet", "…", "named, still collecting runs to judge it"],
};

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function confoundMark(f) {
  const n = (f.confounded || []).length;
  return n ? el("span", { class: "mo-confounded", "data-confounded": n,
                          title: `its window reached across ${plural(n, "other change")} — the reading belongs to them together` },
    ` ⚠${n}`) : null;
}

/** The row's cell: the aims' verdicts when the change named any, else what it removed and
 *  brought in; quiet while a side still lacks its runs. */
export function momentCell(f) {
  if (!f) return el("span", { class: "faint" }, "—");
  const marks = [];
  for (const a of f.aims || []) {
    const [cls, glyph, why] = AIM[a.verdict] || AIM.measuring;
    marks.push(el("span", { class: `mo-mark ${cls}`, "data-aim": a.signature, title: `${a.signature} — ${a.verdict}: ${why}` },
      `${glyph} ${a.signature}`));
  }
  if (!marks.length && f.status === "judged") {
    const fixed = (f.fixed || []).length, brought = (f.introduced || []).length;
    if (fixed) marks.push(el("span", { class: "mo-mark mo-fixed", "data-fixed": fixed,
                                       title: (f.fixed || []).map((r) => r.signature).join(", ") },
      `✓${fixed} gone`));
    if (brought) marks.push(el("span", { class: "mo-mark mo-introduced", "data-introduced": brought,
                                         title: (f.introduced || []).map((r) => r.signature).join(", ") },
      `+${brought} new`));
    if (!marks.length) marks.push(el("span", { class: "faint", title: "no repeated failure came or went" }, "="));
  }
  if (!marks.length) {
    return el("span", { class: "faint", title: `${f.status}: ${f.runs_before} → ${f.runs_after} runs with recorded failures` },
      f.status === "measuring" ? "…" : "∅");
  }
  return el("span", { class: "chg-moment", "data-moment": f.status }, ...marks, confoundMark(f));
}

const ROWS = [
  ["fixed", "gone after", "mo-fixed"],
  ["introduced", "new after", "mo-introduced"],
  ["persists", "still there", "mo-persists"],
];

/** The opened detail: each repeated failure with its runs before → after. */
export function momentDetail(f) {
  if (!f || (!(f.chronic || []).length && !(f.introduced || []).length && !(f.aims || []).length)) return null;
  const rows = [];
  const line = (r, label, cls) => el("tr", { "data-sig": r.signature },
    el("td", { class: "inline mono" }, r.signature),
    el("td", { class: "num inline" }, `${r.before} → ${r.after}`),
    el("td", { class: "inline" }, el("span", { class: `mo-mark ${cls}` }, label)));
  for (const a of f.aims || []) rows.push(line(a, `aimed · ${a.verdict}`, (AIM[a.verdict] || AIM.measuring)[0]));
  if (f.status === "judged") {
    for (const [key, label, cls] of ROWS) for (const r of f[key] || []) rows.push(line(r, label, cls));
  } else {
    for (const r of f.chronic || []) rows.push(line(r, "repeated before", "mo-quiet"));
  }
  return el("table", { class: "list stack sig-table mo-table" },
    el("thead", {}, el("tr", {},
      el("th", { title: "a failure signature (engine/frictions.py)" }, "failure"),
      el("th", { class: "num", title: `runs carrying it: ${f.runs_before} judged before → ${f.runs_after} after` }, "runs before → after"),
      el("th", {}, confoundMark(f)))),
    el("tbody", {}, ...rows));
}
