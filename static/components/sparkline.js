// Week-by-week SPARKLINES for the DEVELOPMENT views (views/changes.js, views/changes-routine.js):
// one reading of the change timeline (readmodels/change_timeline.py) per small multiple, the
// weeks oldest left, all multiples on one shared x so a column reads across the strip.
//
// A sparkline shows SHAPE, not values: no axes, the numbers in the head (the newest reading) and
// in each week's hover title. What it draws:
//
//   the line      — de-emphasis ink, 2px whatever the width; a week with no reading (null) is a
//                   GAP, never a zero, and a reading with no neighbour is a dot;
//   the newest    — one iris dot, the Develop zone's colour;
//   a reference   — dashed, where the series has a "usual" (1.0 for a ratio to the routine's
//                   own median);
//   log & clamp   — a ratio reads on a log scale clamped to its domain (×0.25–×4 by default:
//                   the first rebuilt week can read ×27); a clamped reading sits on the edge
//                   with a caret pointing past it, its true value in the title;
//   the weeks     — a week whose runs were mostly REBUILT from history is shaded, and the
//                   releases first run that week are a tick at the foot, taller for more.
//
// Accessible as one image: role="img" with an aria-label summary (span, newest, low, high,
// gaps); each week's column carries a <title>, which is also the hover readout. Colours are
// base.css tokens through views.css classes, so a theme switch needs no re-render.

import { el, svgEl } from "/static/util.js";

const W = 160, H = 40, TOP = 4, FOOT = 7;          // FOOT: the release-tick band under the plot
const PLOT = H - FOOT - 2;                          // the plot's lowest y
const s = svgEl;

/** Numbers the way a counter wrote them. */
export const fmt = {
  count: (v) => String(Math.round(v)),
  share: (v) => (v > 0 && v < 0.1 ? `${(v * 100).toFixed(1)}%` : `${Math.round(v * 100)}%`),
  ratio: (v) => `×${v >= 10 ? v.toFixed(0) : v.toFixed(2)}`,
  per: (v) => String(Math.round(v * 100) / 100),
};

const MOSTLY_REBUILT = 0.5;

function weekTitle(w, value, f) {
  const parts = [`${w.week} (from ${w.start})`, value == null ? "no reading" : f(value),
                 `${w.runs} run${w.runs === 1 ? "" : "s"}`];
  const rel = (w.releases || []).length;
  if (rel) parts.push(`${rel} release${rel === 1 ? "" : "s"} first run`);
  if (w.rebuilt) parts.push(w.rebuilt === w.runs ? "all rebuilt" : `${w.rebuilt} rebuilt`);
  return parts.join(" · ");
}

/** One series as an <svg>. `pick(week)` → number | null; `lo`/`hi` fix the domain's ends (null:
 *  the data's own), `minSpan` keeps a near-flat series from magnifying noise, `log` reads the
 *  values on a log scale, `ref` draws the "usual" line. */
export function sparkline(weeks, { key, label, pick, format = fmt.per, log = false,
                                   lo = null, hi = null, minSpan = 0, ref = null }) {
  const values = weeks.map((w) => {
    const v = pick(w);
    return v == null || Number.isNaN(v) || (log && v <= 0) ? null : v;
  });
  const known = values.filter((v) => v != null);
  let a = lo ?? (known.length ? Math.min(...known) : 0);
  let b = hi ?? (known.length ? Math.max(...known) : 1);
  if (b - a < minSpan) {                  // widen away from the fixed end
    if (hi != null && lo == null) a = b - minSpan; else b = a + minSpan;
  }
  if (b <= a) b = a + (log ? a : 1) || 1;
  const t = log ? Math.log : (v) => v;
  const y = (v) => {
    const f = (t(Math.min(Math.max(v, a), b)) - t(a)) / (t(b) - t(a));
    return PLOT - f * (PLOT - TOP);
  };
  const band = W / Math.max(weeks.length, 1);
  const x = (i) => band * i + band / 2;

  const gaps = values.filter((v) => v == null).length;
  const newest = known.length ? values.lastIndexOf(known.at(-1)) : -1;
  const summary = known.length
    ? `${label}, ${weeks.length} weeks ${weeks[0].week} to ${weeks.at(-1).week}: newest `
      + `${format(values[newest])} (${weeks[newest].week}), low ${format(Math.min(...known))}, `
      + `high ${format(Math.max(...known))}${gaps ? `, ${gaps} week${gaps === 1 ? "" : "s"} without a reading` : ""}`
    : `${label}: no reading in ${weeks.length} weeks`;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "spark", role: "img",
                         "aria-label": summary, "data-spark": key });

  // the weeks first, under everything: rebuilt shading and the release ticks
  const maxRel = Math.max(1, ...weeks.map((w) => (w.releases || []).length));
  weeks.forEach((w, i) => {
    if (w.runs && w.rebuilt / w.runs > MOSTLY_REBUILT) {
      svg.append(s("rect", { x: band * i, y: 0, width: band, height: H - FOOT,
                             class: "spark-rebuilt", "data-rebuilt-week": w.week }));
    }
    const rel = (w.releases || []).length;
    if (rel) {
      const h = 2 + (FOOT - 2) * Math.sqrt(rel / maxRel);
      svg.append(s("line", { x1: x(i), x2: x(i), y1: H, y2: H - h, class: "spark-rel" }));
    }
  });
  if (ref != null && ref > a && ref < b) {
    svg.append(s("line", { x1: 0, x2: W, y1: y(ref), y2: y(ref), class: "spark-ref" }));
  }

  // the line, broken at every gap; a lone reading is a dot
  let d = "", segments = 0;
  values.forEach((v, i) => {
    if (v == null) return;
    const joined = i > 0 && values[i - 1] != null;
    if (!joined) segments += 1;
    d += `${joined ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    const alone = !joined && (i === values.length - 1 || values[i + 1] == null);
    if (alone && i !== newest) svg.append(s("circle", { cx: x(i), cy: y(v), r: 1.6, class: "spark-dot" }));
  });
  if (d) svg.append(s("path", { d, class: "spark-line", "data-segments": segments }));
  values.forEach((v, i) => {               // a reading past the domain: a caret on its edge
    if (v == null || (v <= b && v >= a)) return;
    const up = v > b, ey = up ? TOP : PLOT, dy = up ? -3 : 3;
    svg.append(s("path", { d: `M${x(i) - 2.5},${ey} L${x(i)},${ey + dy} L${x(i) + 2.5},${ey} Z`,
                           class: "spark-clamp" }));
  });
  if (newest >= 0) {
    svg.append(s("circle", { cx: x(newest), cy: y(values[newest]), r: 2.4, class: "spark-now" }));
  }

  // the hover layer last, on top: one column per week, its title the readout
  weeks.forEach((w, i) => {
    svg.append(s("rect", { x: band * i, y: 0, width: band, height: H, class: "spark-hit",
                           "data-week": w.week, ...(values[i] == null ? { "data-gap": "" } : {}) },
      weekTitle(w, values[i], format)));
  });
  return { svg, newest: newest >= 0 ? format(values[newest]) : "—" };
}

/** The strip: one small multiple per series over the same weeks, and a one-line key. */
export function sparkStrip(weeks, series) {
  if (!weeks.length) return null;
  const rebuilt = weeks.some((w) => w.runs && w.rebuilt / w.runs > MOSTLY_REBUILT);
  const released = weeks.some((w) => (w.releases || []).length);
  return el("div", { class: "spark-strip", "data-weeks": weeks.length },
    el("div", { class: "spark-grid" }, ...series.map((spec) => {
      const { svg, newest } = sparkline(weeks, spec);
      return el("figure", { class: "spark-cell", "data-series": spec.key },
        el("figcaption", { class: "spark-head", title: spec.title || null },
          el("span", { class: "spark-label" }, spec.label),
          el("span", { class: "spark-value", title: "the newest week with a reading" }, newest)),
        svg);
    })),
    el("div", { class: "spark-key faint" },
      el("span", { class: "mono" }, `${weeks[0].week} → ${weeks.at(-1).week}`),
      rebuilt ? el("span", { class: "spark-key-item" },
        el("span", { class: "spark-swatch rebuilt", "aria-hidden": "true" }), "mostly rebuilt") : null,
      released ? el("span", { class: "spark-key-item" },
        el("span", { class: "spark-swatch rel", "aria-hidden": "true" }), "releases") : null));
}
