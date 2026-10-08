// The marks the DEVELOPMENT views speak in (views/changes.js, views/changes-routine.js) and the
// one line the production routine page carries about them (views/routine.js): a change's
// VERDICT as a chip, its three DIMENSIONS as tiny marks, WHAT changed as a compact mono line,
// and each signal's two sides with a ▲▼ mark.
//
// Colour is state, on the chip vocabulary base.css already has: improved is ok, regressed is
// the failure tone, mixed is the warning one, `no effect` and `too few runs` are quiet, and
// `measuring` is the machine still collecting runs (signal, without the pulse a live run wears —
// nothing changes while you look at it). SUMMONS is never a verdict's own colour: it is what the
// production page's line wears when the newest change regressed, because THERE it means a person
// should look (`summonsChip`). Every mark carries its word in a tooltip and a glyph beside its
// colour, so no state is told by colour alone.

import { el, fmtNum } from "/static/util.js";

/** verdict → [chip class, glyph, what it means]. The words are change_effects.py's. */
export const VERDICTS = {
  improved: ["ok", "▲", "some dimension got better and none got worse"],
  regressed: ["failed", "▼", "some dimension got worse and none got better"],
  mixed: ["partial", "◆", "better in one way, worse in another — a person weighs it"],
  "no effect": ["idle", "=", "nothing moved past its noise threshold"],
  measuring: ["v-measuring", "…", "the newest change — its after-window is still filling"],
  "too few runs": ["disabled", "∅", "another change came before either side had enough runs"],
};

/** A verdict as a chip; `text` replaces the word (a routine's slug in the fleet roll-up). */
export function verdictChip(verdict, { text = "", href = "", summons = false } = {}) {
  const [cls, glyph, why] = VERDICTS[verdict] || ["idle", "?", ""];
  const attrs = { class: `chip bare ${summons ? "v-summons" : cls}`, "data-verdict": verdict,
                  title: text ? `${text} — ${verdict}: ${why}` : `${verdict}: ${why}` };
  const body = [el("span", { class: "v-glyph", "aria-hidden": "true" }, glyph), text || verdict];
  return href ? el("a", { ...attrs, href }, ...body) : el("span", attrs, ...body);
}

/** Verdict counts as chips, most telling first: "2 improved", "1 regressed". */
export function verdictCounts(counts) {
  return el("span", { class: "v-chips" },
    ...Object.keys(VERDICTS).filter((v) => counts?.[v])
      .map((v) => verdictChip(v, { text: `${counts[v]} ${v}` })));
}

const DIM_MARK = { better: "+", worse: "−", mixed: "±", same: "·" };
export const DIMENSIONS = ["correctness", "completeness", "effectiveness"];

/** The three dimensions as three tiny marks, in a fixed order; the word is in the tooltip. */
export function dimMarks(dimensions = {}) {
  return el("span", { class: "dim-marks" }, ...DIMENSIONS.map((d) => {
    const state = dimensions[d] || "same";
    return el("span", { class: `dim-mark ${state}`, "data-dim": d, "data-state": state,
                        title: `${d}: ${state}` }, DIM_MARK[state] || "·");
  }));
}

const short = (v) => (v ? String(v).slice(0, 7) : "—");
const HASHED = new Set(["recipe", "config"]);

/** One `what` entry as compact mono text: "recipe 1a2b3c4→3c4d5e6", "model Opus high→Sonnet
 *  high", "rule web-research" (+ added, − removed, the hashes in the tooltip). */
export function whatText(w) {
  if (w.kind === "rule") {
    const sign = !w.from ? "+" : !w.to ? "−" : "";
    return `${sign}rule ${w.name}`;
  }
  const [a, b] = HASHED.has(w.kind) ? [short(w.from), short(w.to)] : [w.from || "—", w.to || "—"];
  return `${w.kind} ${a}→${b}`;
}

export function whatChanged(what = []) {
  return el("span", { class: "chg-what" }, ...what.map((w, i) => [
    i ? el("span", { class: "faint" }, " · ") : null,
    el("span", { title: `${w.kind}${w.name ? ` ${w.name}` : ""}: ${w.from || "—"} → ${w.to || "—"}` },
      whatText(w))]));
}

// How each signal's value reads: a share as a percentage, a median as its unit, a per-run mean
// as a short decimal. The names are change_signals.py's.
const SHARES = new Set(["failed", "partial", "met_rate", "cache_share", "util_failures"]);

export function fmtSignal(name, v) {
  if (v === null || v === undefined) return "—";
  if (SHARES.has(name)) return `${Math.round(v * 100)}%`;
  if (name === "tokens") return fmtNum(v);
  if (name === "elapsed_s") return (v / 60).toFixed(1);
  return String(Math.round(v * 100) / 100);
}

/** ▲ the number went up, ▼ down — coloured only by whether that was better or worse. */
export function signalMark(sig) {
  const { before: b, after: a, verdict } = sig;
  const glyph = b == null || a == null || a === b ? "·" : a > b ? "▲" : "▼";
  const tone = verdict > 0 ? "better" : verdict < 0 ? "worse" : "same";
  return el("span", { class: `sig-mark ${tone}`, "data-tone": tone,
                      title: verdict > 0 ? "better" : verdict < 0 ? "worse" : "within its noise" },
    glyph);
}
