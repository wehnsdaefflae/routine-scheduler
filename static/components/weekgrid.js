// Week strip for the dashboard: one row per scheduled routine, seven day columns starting today,
// every cron fire in view as a duration BAR on a shared timeline. The bar starts at the fire time
// and its width is the routine's average runtime drawn TRUE TO SCALE against a day's width
// (DAY_W px = 24h) — with a small minimum so a short run still shows and the exact value in the
// hover tooltip. Every lane carries its NAME at the left edge — a haloed overlay on the timeline,
// not a label column, so the strip keeps its full width; colour + schedule + avg runtime live in
// the LEGEND below. Times are in the browser's timezone; fires already behind us render dimmed;
// a live cursor marks now. Rows follow the dashboard's own filters, ordered by next upcoming fire.
//
// Groups: an UNSCHEDULED group merges its members' own fires onto one shared lane (F271). A
// SCHEDULED group (one with a cron, D71) goes further — its members' own crons are
// daemon-suppressed (the server sends them no fires, R313), so the lane draws the GROUP's fires:
// at each fire the visible members chain END-TO-END in execution order (the ingest pass, then
// the split members again as the outbound pass, F292), each segment sized by that member's
// average runtime. Segment starts after the first are estimates (a member starts when its
// predecessor finishes) and are marked ~ in the tooltip.
//
// Construction takes optional drag HANDLERS (see weekgrid-drag.js): with them, bars become
// draggable — onto a sibling bar to reorder the group, onto another group's lane to join it,
// onto the remove strip to leave the group, or along their own lane to reschedule.

import { el, fmtDur } from "/static/util.js";
import { SERIES_COLORS } from "/static/components/charts.js";
import { weekDrag } from "/static/components/weekgrid-drag.js";

// Stable color identity: hash the slug into the palette so a routine keeps its
// color across reorders / additions (an index-based pick reshuffles everyone).
function slugColor(slug) {
  let h = 0;
  for (const ch of String(slug)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return SERIES_COLORS[h % SERIES_COLORS.length];
}

const NS = "http://www.w3.org/2000/svg";
const DAY_MS = 86_400_000, DAY_SECONDS = 86_400;
const DAYS = 7, DAY_W = 144, HEAD_H = 22, ROW_H = 22, PAD_B = 8;
// A fire's bar width = its average runtime as a fraction of a day × DAY_W (true to scale), floored
// at MIN_BAR_W so a short run is still a visible mark; the exact value lives in the hover tooltip.
const BAR_H = 8, MIN_BAR_W = 2;
// A chain segment advances the next member's start by at least the time MIN_BAR_W spans, so a
// never-run member still occupies a visible slot and segments stay adjacent, never stacked.
const MIN_STEP_S = (MIN_BAR_W / DAY_W) * DAY_SECONDS;
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

// A 5-run MOVING AVERAGE of wall-clock runtime (F210): mean elapsed_s over the routine's most
// recent runs that recorded a real elapsed_s, capped to MOVING_AVG_RUNS — so the bar tracks
// RECENT runtime and isn't dragged by stale runs still in the heartbeat window. recent_runs is
// NEWEST-FIRST (api_routines: info.runs[:HEARTBEAT_RUNS_N]), so the most recent are the HEAD.
// null when none recorded a real elapsed_s — the bar is then a nub.
const MOVING_AVG_RUNS = 5;
function avgRuntime(card) {
  const durs = (card.recent_runs || [])
    .map((r) => r.elapsed_s)
    .filter((v) => typeof v === "number" && v >= 0)
    .slice(0, MOVING_AVG_RUNS);
  if (!durs.length) return null;
  return { secs: durs.reduce((a, b) => a + b, 0) / durs.length, n: durs.length };
}

// How often the live "now" cursor (and the past/future bar dimming) re-positions itself between
// data refreshes, so the green line tracks real time on an idle dashboard (F230). Cheap: it
// re-runs the last update() with the SAME data — only Date.now() moves. The interval self-clears
// once the grid leaves the DOM (the transcript.js live-poll pattern), so it never leaks.
const NOW_REFRESH_MS = 30_000;

export function weekGrid(dragHandlers = null) {
  const node = el("div", { class: "weekgrid" });
  const drag = dragHandlers ? weekDrag(node, dragHandlers) : null;
  let lastArgs = null;

  // cards: the dashboard's currently visible routines; firesBySlug: Map slug → [ms, …] of
  // recurring cron fires; oneShotsBySlug: Map slug → [ms, …] of armed one-shot fires (rendered
  // as distinct hollow bars); groups: the ordered group records (id, name, members, splitSet,
  // cron, schedule_desc, fires — the GROUP's cron fires, D71).
  function update(cards, firesBySlug, oneShotsBySlug = new Map(), groups = []) {
    lastArgs = [cards, firesBySlug, oneShotsBySlug, groups];
    // A live refresh mid-gesture would tear the dragged bar out from under the pointer —
    // hold this render; the drop's own reload (or the next tick) redraws from fresh truth.
    if (drag?.active()) return;
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const t0 = start.getTime(), span = DAYS * DAY_MS;
    const now = Date.now();
    const inWin = (t) => t >= t0 && t < t0 + span;
    // One MEMBER record per routine in view. A scheduled-group member has no own fires (the
    // server withholds its suppressed cron) but must still resolve here — its lane draws it.
    const members = cards
      .map((c) => ({ c,
        fires: (firesBySlug.get(c.slug) || []).filter(inWin),
        oneShots: (oneShotsBySlug.get(c.slug) || []).filter(inWin) }));
    const bySlug = new Map(members.map((m) => [m.c.slug, m]));

    // Lanes. A routine is placed in the FIRST group that lists it (F271); ungrouped routines
    // each get their own single-member lane. A scheduled group's lane shows when the group
    // fires this week (or a member has a one-shot); an unscheduled group's lane shows when a
    // member's own cron or one-shot lands in view.
    const placed = new Set();
    const lanes = [];
    for (const g of groups) {
      const scheduled = !!g.cron;
      const laneMembers = (g.members || [])
        .map((slug) => bySlug.get(slug))
        .filter((m) => m && !placed.has(m.c.slug)
                         && (scheduled || m.fires.length || m.oneShots.length));
      const groupFires = scheduled ? (g.fires || []).filter(inWin) : [];
      if (!laneMembers.length) continue;
      if (!groupFires.length && !laneMembers.some((m) => m.fires.length || m.oneShots.length))
        continue;
      for (const m of laneMembers) placed.add(m.c.slug);
      lanes.push({ group: g, members: laneMembers, groupFires });
    }
    for (const m of members) {
      if (placed.has(m.c.slug) || (!m.fires.length && !m.oneShots.length)) continue;
      placed.add(m.c.slug);
      lanes.push({ group: null, members: [m], groupFires: [] });
    }
    const nextOf = (ts) => ts.find((t) => t >= now) ?? Infinity;
    const laneUpcoming = (lane) => Math.min(nextOf(lane.groupFires),
      ...lane.members.map((m) => Math.min(nextOf(m.fires), nextOf(m.oneShots))));
    lanes.sort((a, b) => laneUpcoming(a) - laneUpcoming(b));
    const rows = lanes;
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
    const rowsMeta = [];   // per-lane geometry for the drag controller
    const hits = [];       // per-bar geometry: what a pointer can pick up
    rows.forEach((lane, i) => {
      const y = HEAD_H + i * ROW_H, cy = y + ROW_H / 2;
      const g = s("g", { class: "wg-row" });
      const rowbg = s("rect", { x: 0, y, width: W, height: ROW_H, class: "wg-rowbg" });
      g.append(rowbg);
      rowsMeta.push({ lane, y, rowbg });
      const grouped = lane.group != null;
      const grpNote = grouped ? ` · group ${lane.group.name}` : "";
      // The group's REAL schedule stands in for a scheduled member's vestigial own one (R313).
      const schedOf = (m) => (grouped && lane.group.cron)
        ? (lane.group.schedule_desc || "") : (m.c.schedule_desc || "");
      // Every member's OWN bars (individual cron fires — none on scheduled-group lanes — plus
      // one-shots as hollow bars, not draggable: re-arming is the Schedule-once card's job).
      for (const m of lane.members) {
        const color = slugColor(m.c.slug);
        const name = m.c.name || m.c.slug;
        const avg = avgRuntime(m.c);
        const runNote = avg ? ` · runs ~${fmtDur(avg.secs)}` : " · never run";
        const a = s("a", { href: `#/routine/${m.c.slug}` });   // a bar opens its routine
        for (const t of m.fires) {
          const r = s("rect", { x: x(t), y: cy - BAR_H / 2, width: barWidth(avg?.secs ?? 0, x(t)), height: BAR_H,
            rx: BAR_H / 2, fill: color, class: t < now ? "wg-bar past" : "wg-bar" },
            `${name}${grpNote} · ${fmtAt.format(new Date(t))}${runNote}`);
          a.append(r);
          hits.push({ rect: r, laneIdx: i, slug: m.c.slug, name, kind: "fire", start: t });
        }
        for (const t of m.oneShots)
          a.append(s("rect", { x: x(t), y: cy - BAR_H / 2, width: barWidth(avg?.secs ?? 0, x(t)), height: BAR_H,
            rx: BAR_H / 2, fill: "none", stroke: color, "stroke-width": 1.4,
            class: t < now ? "wg-bar one-shot past" : "wg-bar one-shot" },
            `${name}${grpNote} · one-shot · ${fmtAt.format(new Date(t))}${runNote}`));
        g.append(a);
        legendItems.push({ slug: m.c.slug, name, color, sched: schedOf(m),
          avg, group: lane.group?.name });
      }
      // The chain (D71): at each GROUP fire the visible members run back-to-back — ingest
      // pass in member order, then the split members again as the outbound pass (F292).
      if (lane.groupFires.length) {
        const split = lane.group.splitSet || new Set();
        const hasSplit = lane.members.some((m) => split.has(m.c.slug));
        const seq = lane.members.map((m) => ({ m, phase: hasSplit ? "ingest" : "" }));
        if (hasSplit)
          for (const m of lane.members)
            if (split.has(m.c.slug)) seq.push({ m, phase: "outbound" });
        for (const t of lane.groupFires) {
          let cur = t;
          seq.forEach(({ m, phase }, si) => {
            const xt = x(cur);
            if (xt >= W) return;   // this chain's tail runs off the strip
            const avg = avgRuntime(m.c);
            const name = m.c.name || m.c.slug;
            const runNote = avg ? ` · runs ~${fmtDur(avg.secs)}` : " · never run";
            const at = (si ? "~" : "") + fmtAt.format(new Date(cur));
            const a = s("a", { href: `#/routine/${m.c.slug}` });
            const r = s("rect", { x: xt, y: cy - BAR_H / 2, width: barWidth(avg?.secs ?? 0, xt),
              height: BAR_H, rx: BAR_H / 2, fill: slugColor(m.c.slug),
              class: cur < now ? "wg-bar past" : "wg-bar" },
              `${name} · group ${lane.group.name}${phase ? ` · ${phase}` : ""} · ${at}${runNote}`);
            a.append(r);
            g.append(a);
            hits.push({ rect: r, laneIdx: i, slug: m.c.slug, name, kind: "seg",
                        start: cur, fireT: t });
            cur += Math.max(avg?.secs ?? 0, MIN_STEP_S) * 1000;
          });
        }
      }
      // The lane's name, haloed over the timeline (labels are display-only — pointer events
      // pass through to the bars beneath; navigation lives on the bars and in the legend).
      const label = grouped ? `⛓ ${lane.group.name}` : (lane.members[0].c.name || lane.members[0].c.slug);
      g.append(text(4, cy + 3.5, label, grouped ? "wg-lane-label group" : "wg-lane-label"));
      svg.append(g);
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
        it.sched ? el("span", { class: "wg-leg-sched" }, it.sched) : null,
        it.group ? el("span", { class: "wg-leg-group" }, `⛓ ${it.group}`) : null));

    node.append(svg, legend);
    drag?.setLayout({ svg, rows: rowsMeta, hits, t0, span, W, headH: HEAD_H, rowH: ROW_H });
  }

  // Advance the live "now" cursor between data refreshes; self-clears when the grid unmounts.
  const tick = setInterval(() => {
    if (!document.body.contains(node)) return void clearInterval(tick);
    if (lastArgs) update(...lastArgs);
  }, NOW_REFRESH_MS);

  return { node, update };
}
