// Week strip for the dashboard: one row per scheduled routine, seven day columns
// starting today, every cron fire in view as a dot on a shared timeline. Times are
// bucketed in the browser's timezone (like every timestamp in the UI); fires already
// behind us render dimmed; a live cursor marks now. Rows follow the dashboard's own
// filters, ordered by next upcoming fire.

import { el } from "/static/util.js";
import { SERIES_COLORS } from "/static/components/charts.js";

const NS = "http://www.w3.org/2000/svg";
const DAY_MS = 86_400_000;
const DAYS = 7, LABEL_W = 168, DAY_W = 116, HEAD_H = 22, ROW_H = 24, PAD_B = 8;
const W = LABEL_W + DAYS * DAY_W;

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

export function weekGrid() {
  const node = el("div", { class: "weekgrid" });

  // cards: the dashboard's currently visible routines; firesBySlug: Map slug → [ms, …]
  function update(cards, firesBySlug) {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const t0 = start.getTime(), span = DAYS * DAY_MS;
    const now = Date.now();
    const rows = cards
      .map((c) => ({ c, fires: (firesBySlug.get(c.slug) || []).filter((t) => t >= t0 && t < t0 + span) }))
      .filter((r) => r.fires.length)
      .sort((a, b) => (a.fires.find((t) => t >= now) ?? Infinity) - (b.fires.find((t) => t >= now) ?? Infinity));
    node.replaceChildren();
    if (!rows.length) {
      node.append(el("div", { class: "faint small", style: "padding:4px 2px" },
        "nothing scheduled among the routines in view"));
      return;
    }
    const H = HEAD_H + rows.length * ROW_H + PAD_B;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "wg", role: "img",
                           "aria-label": "scheduled fire times over the coming week" });
    const x = (t) => LABEL_W + ((t - t0) / span) * (W - LABEL_W);

    for (let d = 0; d < DAYS; d++) {
      const dx = LABEL_W + d * DAY_W;
      if (d === 0) svg.append(s("rect", { x: dx, y: HEAD_H, width: DAY_W, height: H - HEAD_H - PAD_B, class: "wg-today" }));
      svg.append(s("line", { x1: dx, y1: HEAD_H, x2: dx, y2: H - PAD_B, class: "wg-grid" }));
      svg.append(text(dx + 6, 14, d === 0 ? "TODAY" : fmtDay.format(new Date(t0 + d * DAY_MS)).toUpperCase(), "wg-day"));
    }
    svg.append(s("line", { x1: W, y1: HEAD_H, x2: W, y2: H - PAD_B, class: "wg-grid" }));

    rows.forEach((r, i) => {
      const y = HEAD_H + i * ROW_H, cy = y + ROW_H / 2;
      const color = SERIES_COLORS[i % SERIES_COLORS.length];
      const g = s("g", { class: "wg-row" });
      g.append(s("rect", { x: 0, y, width: W, height: ROW_H, class: "wg-rowbg" }));
      const name = r.c.name || r.c.slug;
      const a = s("a", { href: `#/routine/${r.c.slug}` }, `${name} — ${r.c.schedule_desc || ""}`);
      a.append(s("circle", { cx: 12, cy, r: 3.2, fill: color }));
      a.append(text(24, cy + 3.5, name.length > 22 ? `${name.slice(0, 21)}…` : name, "wg-name"));
      g.append(a);
      for (const t of r.fires)
        g.append(s("circle", { cx: x(t), cy, r: 3.2, fill: color, class: t < now ? "wg-dot past" : "wg-dot" },
          `${name} · ${fmtAt.format(new Date(t))}`));
      svg.append(g);
    });

    if (now >= t0 && now < t0 + span)
      svg.append(s("line", { x1: x(now), y1: HEAD_H - 4, x2: x(now), y2: H - PAD_B, class: "wg-now" }, "now"));
    node.append(svg);
  }

  return { node, update };
}
