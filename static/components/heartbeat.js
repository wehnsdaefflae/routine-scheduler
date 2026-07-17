// Heartbeat strip — one routine's last runs as a compact SVG bar row (the symmetric PAST
// view to the week grid's future fires): oldest left, newest at the RIGHT edge, one bar
// per run. Color = outcome bucket (ok green / partial amber / failed red / aborted grey /
// still-active teal — partial comes from status.json `outcome`, since `state` folds it
// into finished); bar height tracks the run's token spend, sqrt-scaled against the
// strip's own max so one huge run doesn't flatline the rest. Hover shows
// ts · outcome · turns · tokens · cost · duration; click opens that run. Missing history
// pads with faint stubs so every strip spans the same width across cards.

import { fmtAbs, fmtDur, fmtNum, fmtUsd } from "/static/util.js";

const NS = "http://www.w3.org/2000/svg";
const SLOTS = 15, BAR_W = 7, GAP = 3, H = 24, MIN_H = 5, MAX_H = 20;
const W = SLOTS * (BAR_W + GAP) - GAP;

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

function bucket(run) {
  if (run.state === "failed") return "failed";
  if (run.state === "aborted") return "aborted";
  if (run.state === "finished") return run.outcome === "partial" ? "partial" : "ok";
  return "active";   // WORKING states + waiting_user/paused — the in-flight slot
}

const LABEL = {
  ok: "ok", partial: "partial — stopped early", failed: "failed", aborted: "aborted",
  active: "still running",
};

// runs: the card's recent_runs (newest first, ≤15) — see api_routines HEARTBEAT_RUNS_N.
export function heartbeat(runs) {
  const shown = (runs || []).slice(0, SLOTS).reverse();   // render oldest → newest
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "heartbeat", role: "img",
                         "aria-label": "recent run history — newest right, click a bar to open that run" });
  const maxTok = Math.max(...shown.map((r) => r.tokens || 0), 0);
  const pad = SLOTS - shown.length;
  for (let i = 0; i < pad; i++)
    svg.append(s("rect", { x: i * (BAR_W + GAP), y: H - 2, width: BAR_W, height: 2,
                           rx: 1, class: "hb-empty" }));
  shown.forEach((r, j) => {
    const x = (pad + j) * (BAR_W + GAP);
    const h = maxTok > 0 ? MIN_H + (MAX_H - MIN_H) * Math.sqrt((r.tokens || 0) / maxTok)
                         : (MIN_H + MAX_H) / 2;
    const b = bucket(r);
    const parts = [fmtAbs(r.ts), LABEL[b]];
    if (r.turns) parts.push(`${r.turns} turns`);
    if (r.tokens) parts.push(`${fmtNum(r.tokens)} tok`);
    if (r.cost > 0) parts.push(fmtUsd(r.cost));
    if (r.elapsed_s != null) parts.push(fmtDur(r.elapsed_s));
    const a = s("a", { href: `#/run/${r.run_id}`, class: "hb-bar" }, parts.join(" · "));
    a.append(s("rect", { x, y: 0, width: BAR_W, height: H, fill: "transparent" }));
    a.append(s("rect", { x, y: H - h, width: BAR_W, height: h, rx: 1.5, class: `hb-${b}` }));
    svg.append(a);
  });
  return svg;
}
