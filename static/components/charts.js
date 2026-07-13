// Configurable SVG charts for the Stats tab — dependency-free, dark-theme.
// The categorical palette is validated (lightness band, chroma, CVD separation,
// contrast) against the panel surface #101720; fixed assignment order, never cycled:
// series beyond the sixth fold into a gray "other". Colors follow the ENTITY (a key
// keeps its color when the range filter changes), text wears text tokens only.

import { el } from "/static/util.js";

export const SERIES_COLORS = ["#cc7f1f", "#3d8fe0", "#219e8e", "#a86fd1", "#d16a92", "#7fa03f"];
export const OTHER_COLOR = "#56697e";
const MAX_SERIES = 6;

export const METRICS = {
  runs: { label: "runs", of: () => 1, fmt: (v) => String(Math.round(v)) },
  tokens: { label: "tokens", of: (r) => r.tokens_in + r.tokens_out, fmt: fmtTokens },
  tokens_in: { label: "tokens in", of: (r) => r.tokens_in, fmt: fmtTokens },
  tokens_out: { label: "tokens out", of: (r) => r.tokens_out, fmt: fmtTokens },
  cost: { label: "cost", of: (r) => r.cost, fmt: (v) => "$" + v.toFixed(v && v < 1 ? 3 : 2) },
  minutes: { label: "compute minutes", of: (r) => r.elapsed_s / 60, fmt: (v) => v.toFixed(v < 10 ? 1 : 0) + "m" },
};
export const GROUPS = {
  none: "total", routine: "by routine", kind: "by kind", model: "by model",
  endpoint: "by endpoint", state: "by outcome",
};
export const RANGES = [7, 14, 30, 90];

function fmtTokens(n) {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(Math.round(n));
}

function lastDays(n) {
  const out = [];
  const d = new Date();
  for (let i = n - 1; i >= 0; i--) {
    const t = new Date(d.getTime() - i * 86400_000);
    out.push(t.toISOString().slice(0, 10));
  }
  return out;
}

// Entity-stable color assignment: rank every key of a group dimension over the WHOLE
// dataset once — a range filter that drops series must never repaint the survivors.
export function colorMap(runs, group) {
  if (group === "none") return () => SERIES_COLORS[0];
  const totals = {};
  for (const r of runs) {
    const k = r[group] || "unknown";
    totals[k] = (totals[k] || 0) + r.tokens_in + r.tokens_out + 1;
  }
  const ranked = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
  const m = new Map(ranked.slice(0, MAX_SERIES).map((k, i) => [k, SERIES_COLORS[i]]));
  return (key) => (key === "other" ? OTHER_COLOR : m.get(key) || OTHER_COLOR);
}

// ---- shared tooltip (one fixed node for every chart on the page) ----------------
let tip = null;
function showTip(evt, lines) {
  if (!tip) {
    tip = el("div", { class: "chart-tip" });
    document.body.append(tip);
  }
  tip.replaceChildren(...lines.map((l) => el("div", {}, l)));
  tip.hidden = false;
  const pad = 12;
  const w = tip.offsetWidth || 120;
  tip.style.left = Math.min(evt.clientX + pad, window.innerWidth - w - pad) + "px";
  tip.style.top = (evt.clientY + pad) + "px";
}
export function hideTip() { if (tip) tip.hidden = true; }

const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

// ---- the chart: {metric, group, range, type} × per-run records → node ------------
export function chartNode(spec, runs, colorOf) {
  const metric = METRICS[spec.metric] || METRICS.tokens;
  const days = lastDays(spec.range || 30);
  const inRange = new Set(days);
  const rows = runs.filter((r) => inRange.has(r.day));

  // bucket day × series
  const seriesTotals = {};
  const buckets = new Map(days.map((d) => [d, {}]));
  for (const r of rows) {
    const key = spec.group === "none" ? "total" : (r[spec.group] || "unknown");
    const v = metric.of(r);
    const b = buckets.get(r.day);
    b[key] = (b[key] || 0) + v;
    seriesTotals[key] = (seriesTotals[key] || 0) + v;
  }
  let keys = Object.keys(seriesTotals).sort((a, b) => seriesTotals[b] - seriesTotals[a]);
  if (keys.length > MAX_SERIES) {                       // fold the tail, never a 7th hue
    const folded = keys.slice(MAX_SERIES);
    keys = [...keys.slice(0, MAX_SERIES), "other"];
    for (const b of buckets.values()) {
      for (const k of folded) {
        if (k in b) { b.other = (b.other || 0) + b[k]; delete b[k]; }
      }
    }
  }
  if (!rows.length) {
    return el("div", { class: "faint small", style: "padding:18px 4px" },
      "no runs in this range");
  }

  const W = 720, H = 200, L = 46, R = 8, T = 8, B = 22;
  const iw = W - L - R, ih = H - T - B;
  const stack = spec.type !== "line";
  const dayMax = Math.max(...days.map((d) => {
    const b = buckets.get(d);
    const vals = keys.map((k) => b[k] || 0);
    return stack ? vals.reduce((a, v) => a + v, 0) : Math.max(0, ...vals);
  }), 1e-9);
  const yMax = niceCeil(dayMax);
  const y = (v) => T + ih - (v / yMax) * ih;
  const xw = iw / days.length;
  const x = (i) => L + i * xw;

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img" });

  // recessive grid + y tick labels (text tokens, never series color)
  for (const f of [0, 0.5, 1]) {
    const gy = y(yMax * f);
    svg.append(s("line", { x1: L, x2: W - R, y1: gy, y2: gy, class: "chart-grid" }));
    const lbl = s("text", { x: L - 6, y: gy + 3, class: "chart-ylabel", "text-anchor": "end" });
    lbl.textContent = metric.fmt(yMax * f);
    svg.append(lbl);
  }
  // x labels: at most ~8, collision-free
  const step = Math.ceil(days.length / 8);
  days.forEach((d, i) => {
    if (i % step) return;
    const lbl = s("text", { x: x(i) + xw / 2, y: H - 6, class: "chart-xlabel", "text-anchor": "middle" });
    lbl.textContent = d.slice(5);
    svg.append(lbl);
  });

  const tipLines = (d) => {
    const b = buckets.get(d);
    const present = keys.filter((k) => b[k]);
    return [d, ...present.map((k) => `${k}: ${metric.fmt(b[k])}`),
            ...(present.length > 1 && stack
                ? [`total: ${metric.fmt(present.reduce((a, k) => a + b[k], 0))}`] : [])];
  };

  if (stack) {
    // bars: baseline-anchored, 2px surface gap between adjacent bars and stacked segments
    const bw = Math.max(2, xw - 2);
    days.forEach((d, i) => {
      const b = buckets.get(d);
      let acc = 0;
      for (const k of [...keys].reverse()) {            // biggest series ends on top
        const v = b[k] || 0;
        if (v <= 0) continue;
        const y1 = y(acc + v), y0 = y(acc);
        svg.append(s("rect", {
          x: x(i) + 1, y: y1, width: bw, height: Math.max(1, y0 - y1),
          fill: colorOf(k), stroke: "var(--surface)", "stroke-width": 1,
          ...(acc === 0 && spec.group === "none" ? { rx: 2 } : {}),
        }));
        acc += v;
      }
      const hit = s("rect", { x: x(i), y: T, width: xw, height: ih, fill: "transparent" });
      hit.addEventListener("mousemove", (e) => showTip(e, tipLines(d)));
      hit.addEventListener("mouseleave", hideTip);
      svg.append(hit);
    });
  } else {
    // lines: 2px strokes, dot on hover via a shared crosshair hit column per day
    for (const k of keys) {
      const pts = days.map((d, i) => `${x(i) + xw / 2},${y(buckets.get(d)[k] || 0)}`);
      svg.append(s("polyline", { points: pts.join(" "), fill: "none",
                                 stroke: colorOf(k), "stroke-width": 2,
                                 "stroke-linejoin": "round" }));
    }
    days.forEach((d, i) => {
      const hit = s("rect", { x: x(i), y: T, width: xw, height: ih, fill: "transparent" });
      hit.addEventListener("mousemove", (e) => showTip(e, tipLines(d)));
      hit.addEventListener("mouseleave", hideTip);
      svg.append(hit);
    });
  }

  const node = el("div", {}, svg);
  if (keys.length > 1) {                                 // ≥2 series → legend, always
    node.append(el("div", { class: "chart-legend" },
      ...keys.map((k) => el("span", { class: "chart-key" },
        el("span", { class: "chart-swatch", style: `background:${colorOf(k)}` }), k))));
  }
  return node;
}

function niceCeil(v) {
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (v <= m * mag) return m * mag;
  }
  return 10 * mag;
}
