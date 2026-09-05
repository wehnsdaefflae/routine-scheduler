// The watch ribbon — the one thing on every page.
//
// This console exists because work happens while nobody is watching it, so the question it should
// never make you navigate to answer is "did anything happen?". The ribbon is a single band of the
// last 24 hours and the next 6: every run that ran, coloured by how it ended, and every fire still
// to come, hollow. A hairline marks now.
//
// It is deliberately not the week strip. The week strip on the Routines page is a SCHEDULING tool
// — one row per routine or fire lane, drag a bar to move a fire. This is a GLANCE: a single band
// with everything on it, no interaction beyond following a mark to its run. Collapsing them into
// one control would make the glance heavy and the tool cramped.
//
// Cheap by construction: two endpoints the console already polls, one refetch per bus event
// coalesced into a window, and nothing at all while it is collapsed.

import { api } from "/static/api.js";
import { el, fmtTs, storage, svgEl, toDate } from "/static/util.js";
import { slugColor } from "/static/components/charts.js";
import { WORKING } from "/static/states.js";

const HOUR_MS = 3_600_000;
const BACK_H = 24;                 // hours of history in view
const AHEAD_H = 6;                 // hours of schedule in view
const H = 26, LANE_Y = 7, LANE_H = 9, MIN_W = 3;
const COLLAPSE_KEY = "rsched_ribbon_collapsed";
const REFRESH_MIN_MS = 20_000;

// The run index speaks the registry's vocabulary (ok / partial / failed / aborted) and the live
// states on top of it; anything still moving reads as live, whatever it is called.
const isLive = (state) => WORKING.has(state) || state === "waiting_user";
const outcome = (state) => (isLive(state) ? "live"
  : state === "ok" || state === "finished" ? "ok"
  : state === "failed" ? "failed" : "partial");

/** Runs in view, newest first, each with a start and an end in epoch ms. */
function runSpans(runs, from, to) {
  const out = [];
  for (const r of runs) {
    const started = toDate(r.ts || r.started);
    if (!started) continue;
    const t0 = started.getTime();
    const t1 = Math.min(to, t0 + Math.max(60, r.elapsed_s || 60) * 1000);
    if (t1 < from) continue;
    out.push({ t0: Math.max(from, t0), t1: isLive(r.state) ? Date.now() : t1,
               slug: r.routine || r.slug || "", run: r, kind: outcome(r.state) });
  }
  return out;
}

/** Every upcoming cron fire in view, from the same payload the week strip reads. */
function fireTimes(week, from, to) {
  const rows = [...(week.routines || []), ...(week.lanes || [])];
  const out = [];
  for (const row of rows) {
    for (const iso of [...(row.fires || []), ...(row.one_shots || [])]) {
      const t = toDate(iso)?.getTime();
      if (t != null && t >= from && t <= to) out.push({ t, label: row.slug || row.name });
    }
  }
  return out;
}

export function mountRibbon(host) {
  let collapsed = storage.get(COLLAPSE_KEY) === "1";
  let timer = null, last = 0, pending = false;

  const label = el("span", { class: "ribbon-label" }, "watch");
  const summary = el("span", { class: "ribbon-summary" }, "");
  const track = el("div", { class: "ribbon-track" });
  const toggle = el("button", {
    class: "ribbon-toggle", type: "button",
    title: "show or hide the watch ribbon (remembered on this browser)",
  }, collapsed ? "show" : "hide");
  const bar = el("div", { class: `ribbon${collapsed ? " collapsed" : ""}` },
    label, track, summary, toggle);
  host.replaceChildren(bar);

  toggle.onclick = () => {
    collapsed = !collapsed;
    storage.set(COLLAPSE_KEY, collapsed ? "1" : "0");
    bar.classList.toggle("collapsed", collapsed);
    toggle.textContent = collapsed ? "show" : "hide";
    if (!collapsed) refresh();
  };

  function paint(runs, week) {
    const now = Date.now();
    const from = now - BACK_H * HOUR_MS, to = now + AHEAD_H * HOUR_MS;
    const width = Math.max(240, track.clientWidth || 240);
    const x = (t) => ((t - from) / (to - from)) * width;

    const svg = svgEl("svg", { viewBox: `0 0 ${width} ${H}`, preserveAspectRatio: "none",
                               role: "img", "aria-label": "runs in the last day" });

    // hour rules every 6h, labelled — enough to read "overnight" off the band
    for (let t = Math.ceil(from / (6 * HOUR_MS)) * 6 * HOUR_MS; t < to; t += 6 * HOUR_MS) {
      svg.append(svgEl("line", { class: "rb-hour", x1: x(t), x2: x(t), y1: 0, y2: H - 9 }));
      const d = new Date(t);
      const tick = svgEl("text", { class: "rb-tick", x: x(t) + 3, y: H - 1 });
      tick.textContent = `${String(d.getHours()).padStart(2, "0")}:00`;
      svg.append(tick);
    }

    for (const f of fireTimes(week, now, to)) {
      const m = svgEl("rect", { class: "rb-fire", x: x(f.t) - 1.5, y: LANE_Y, width: 3, height: LANE_H });
      m.append(svgEl("title", {})).lastChild.textContent = `${f.label} · scheduled ${fmtTs(new Date(f.t).toISOString())}`;
      svg.append(m);
    }

    const spans = runSpans(runs, from, to);
    for (const s of spans) {
      const a = svgEl("a", { class: "rb-mark", href: `#/run/${s.run.run_id}` });
      const rect = svgEl("rect", {
        class: `rb-run ${s.kind}`, x: x(s.t0), y: LANE_Y,
        width: Math.max(MIN_W, x(s.t1) - x(s.t0)), height: LANE_H,
      });
      if (s.kind === "ok") rect.setAttribute("fill", slugColor(s.slug));
      const t = svgEl("title", {});
      t.textContent = `${s.slug} · ${s.run.state} · ${fmtTs(s.run.ts || s.run.started)}`;
      rect.append(t);
      a.append(rect);
      svg.append(a);
    }

    svg.append(svgEl("line", { class: "rb-now", x1: x(now), x2: x(now), y1: 1, y2: H - 8 }));
    track.replaceChildren(svg);

    const failed = spans.filter((s) => s.kind === "failed").length;
    const live = spans.filter((s) => s.kind === "live").length;
    const parts = [`${spans.length} run${spans.length === 1 ? "" : "s"} in 24h`];
    if (failed) parts.push(`${failed} failed`);
    if (live) parts.push(`${live} running`);
    summary.textContent = parts.join(" · ");
    label.title = `the last ${BACK_H} hours and the next ${AHEAD_H}`;
  }

  async function refresh() {
    if (collapsed) return;
    last = Date.now();
    try {
      const [runs, week] = await Promise.all([
        api("/api/runs?limit=200"),
        api("/api/schedule/week?days=1").catch(() => ({ routines: [], lanes: [] })),
      ]);
      paint(Array.isArray(runs) ? runs : [], week || {});
    } catch {
      // The daemon lamp already says the link is down; a broken ribbon says it twice and
      // steals the row. Leave whatever was last painted.
    }
  }

  // Coalesce: the bus can storm during a busy run, and the ribbon is a glance, not a monitor.
  function schedule() {
    if (pending) return;
    const wait = Math.max(0, REFRESH_MIN_MS - (Date.now() - last));
    pending = true;
    setTimeout(() => { pending = false; refresh(); }, wait);
  }

  const onBus = (ev) => {
    const kind = ev.detail?.event;
    if (kind === "llm_task") return;
    schedule();
  };
  window.addEventListener("rsched-bus", onBus);
  const onResize = () => schedule();
  window.addEventListener("resize", onResize);
  timer = setInterval(refresh, 120_000);
  refresh();

  return () => {
    window.removeEventListener("rsched-bus", onBus);
    window.removeEventListener("resize", onResize);
    clearInterval(timer);
  };
}
