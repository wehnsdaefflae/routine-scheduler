// Week strip for the dashboard: one row per scheduled routine, seven day columns starting today,
// every cron fire in view as a duration BAR on a shared timeline. The bar starts at the fire time
// and its width is the routine's average runtime drawn TRUE TO SCALE against a day's width
// (DAY_W px = 24h) — with a small minimum so a short run still shows and the exact value in the
// hover tooltip. Routine identity (colour + name + schedule) is a LEGEND below the strip, which
// lets the timeline itself use the full width. Times are in the browser's timezone; fires already
// behind us render dimmed; a live cursor marks now. Rows follow the dashboard's own filters,
// ordered by next upcoming fire.

import { el, fmtDur } from "/static/util.js";
import { SERIES_COLORS } from "/static/components/charts.js";

// Stable color identity: hash the slug into the palette so a routine keeps its
// color across reorders / additions (an index-based pick reshuffles everyone).
function slugColor(slug) {
  let h = 0;
  for (const ch of String(slug)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return SERIES_COLORS[h % SERIES_COLORS.length];
}

const NS = "http://www.w3.org/2000/svg";
const DAY_MS = 86_400_000, DAY_SECONDS = 86_400;
// No left label column any more — routine identity moved to the legend below, so the day columns
// fill the whole width and a runtime bar can be drawn to scale against a day.
const DAYS = 7, DAY_W = 144, HEAD_H = 22, ROW_H = 22, PAD_B = 8;
// A fire's bar width = its average runtime as a fraction of a day × DAY_W (true to scale), floored
// at MIN_BAR_W so a short run is still a visible mark; the exact value lives in the hover tooltip.
const BAR_H = 8, MIN_BAR_W = 2;
const W = DAYS * DAY_W;

const fmtDay = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric" });
const fmtAt = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });

function s(tag, attrs = {}, title = "") {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (title) {
    const t = document.createElementNS(NS, "title");
    t.textContent = title;
    n.append(t);
  }
  return n;
}

function text(x, y, str, cls, anchor = "start") {
  const n = s("text", { x, y, class: cls, "text-anchor": anchor });
  n.textContent = str;
  return n;
}

// Mean wall-clock over the routine's recent runs (the heartbeat window the card already carries),
// counting only runs that recorded a real elapsed_s. null when none have — the bar is then a nub.
function avgRuntime(card) {
  const durs = (card.recent_runs || [])
    .map((r) => r.elapsed_s)
    .filter((v) => typeof v === "number" && v >= 0);
  if (!durs.length) return null;
  return { secs: durs.reduce((a, b) => a + b, 0) / durs.length, n: durs.length };
}

export function weekGrid() {
  const node = el("div", { class: "weekgrid" });

  // cards: the dashboard's currently visible routines; firesBySlug: Map slug → [ms, …] of
  // recurring cron fires; oneShotsBySlug: Map slug → [ms, …] of armed one-shot fires (rendered
  // as distinct hollow bars). A row shows if it has either kind in view.
  function update(cards, firesBySlug, oneShotsBySlug = new Map()) {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const t0 = start.getTime(), span = DAYS * DAY_MS;
    const now = Date.now();
    const inWin = (t) => t >= t0 && t < t0 + span;
    const nextUpcoming = (r) => Math.min(
      r.fires.find((t) => t >= now) ?? Infinity,
      r.oneShots.find((t) => t >= now) ?? Infinity);
    const rows = cards
      .map((c) => ({ c,
        fires: (firesBySlug.get(c.slug) || []).filter(inWin),
        oneShots: (oneShotsBySlug.get(c.slug) || []).filter(inWin) }))
      .filter((r) => r.fires.length || r.oneShots.length)
      .sort((a, b) => nextUpcoming(a) - nextUpcoming(b));
    node.replaceChildren();
    if (!rows.length) {
      node.append(el("div", { class: "faint small", style: "padding:4px 2px" },
        "nothing scheduled among the routines in view"));
      return;
    }
    const H = HEAD_H + rows.length * ROW_H + PAD_B;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "wg", role: "img",
                           "aria-label": "scheduled fire times over the coming week" });
    const x = (t) => ((t - t0) / span) * W;

    for (let d = 0; d < DAYS; d++) {
      const dx = d * DAY_W;
      if (d === 0) svg.append(s("rect", { x: dx, y: HEAD_H, width: DAY_W, height: H - HEAD_H - PAD_B, class: "wg-today" }));
      svg.append(s("line", { x1: dx, y1: HEAD_H, x2: dx, y2: H - PAD_B, class: "wg-grid" }));
      svg.append(text(dx + 6, 14, d === 0 ? "TODAY" : fmtDay.format(new Date(t0 + d * DAY_MS)).toUpperCase(), "wg-day"));
    }
    svg.append(s("line", { x1: W - 0.5, y1: HEAD_H, x2: W - 0.5, y2: H - PAD_B, class: "wg-grid" }));

    // Bar width to scale against a day's width, floored; clamped so it never runs off the strip.
    const barWidth = (secs, xt) => Math.min(Math.max(MIN_BAR_W, (secs / DAY_SECONDS) * DAY_W), W - xt);
    const legendItems = [];
    rows.forEach((r, i) => {
      const y = HEAD_H + i * ROW_H, cy = y + ROW_H / 2;
      const color = slugColor(r.c.slug);
      const name = r.c.name || r.c.slug;
      const avg = avgRuntime(r.c);
      const runNote = avg ? ` · runs ~${fmtDur(avg.secs)}` : " · never run";
      const g = s("g", { class: "wg-row" });
      g.append(s("rect", { x: 0, y, width: W, height: ROW_H, class: "wg-rowbg" }));
      const a = s("a", { href: `#/routine/${r.c.slug}` });   // clicking a bar opens the routine
      for (const t of r.fires)
        a.append(s("rect", { x: x(t), y: cy - BAR_H / 2, width: barWidth(avg?.secs ?? 0, x(t)), height: BAR_H,
          rx: BAR_H / 2, fill: color, class: t < now ? "wg-bar past" : "wg-bar" },
          `${name} · ${fmtAt.format(new Date(t))}${runNote}`));
      for (const t of r.oneShots)
        a.append(s("rect", { x: x(t), y: cy - BAR_H / 2, width: barWidth(avg?.secs ?? 0, x(t)), height: BAR_H,
          rx: BAR_H / 2, fill: "none", stroke: color, "stroke-width": 1.4,
          class: t < now ? "wg-bar one-shot past" : "wg-bar one-shot" },
          `${name} · one-shot · ${fmtAt.format(new Date(t))}${runNote}`));
      g.append(a);
      svg.append(g);
      legendItems.push({ slug: r.c.slug, name, color, sched: r.c.schedule_desc || "", avg });
    });

    if (now >= t0 && now < t0 + span)
      svg.append(s("line", { x1: x(now), y1: HEAD_H - 4, x2: x(now), y2: H - PAD_B, class: "wg-now" }, "now"));

    // Legend below the strip: colour → routine, with schedule; exact average runtime on hover.
    const legend = el("div", { class: "wg-legend" });
    for (const it of legendItems)
      legend.append(el("a", { class: "wg-leg", href: `#/routine/${it.slug}`,
        title: it.avg ? `avg runtime ~${fmtDur(it.avg.secs)} over ${it.avg.n} run${it.avg.n > 1 ? "s" : ""}`
                      : "no runs recorded yet" },
        el("span", { class: "wg-swatch", style: `background:${it.color}` }),
        el("span", { class: "wg-leg-name" }, it.name),
        it.sched ? el("span", { class: "wg-leg-sched" }, it.sched) : null));

    node.append(svg, legend);
  }

  return { node, update };
}
