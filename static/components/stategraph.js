// A routine's state graph as a simple highlighted chain: one node per stage module
// (server-side, /stategraph — the modules ARE the states, nothing parsed from prose),
// the CURRENT phase lit up, states before it dimmed as done. setPhase() re-highlights
// live — the run SSE `state` events carry phase transitions (the engine stamps the
// stage module each read_file enters into status.json).
// A recorded phase that matches no stage module is appended as its own node: the diagram
// never lies about where the run says it is. With a `statsUrl` (/api/runs/…/phases) each
// visited node also shows its instrumentation — turns · tokens · wall-clock · cost —
// refreshed on every phase transition: the rail is the run's instrument panel. A state
// BEFORE the current one that no turn ever ran under (the run never read its module)
// is marked "skipped" rather than checked off — passed, not done.

import { api } from "/static/api.js";
import { el, fmtCost, fmtDur, fmtNum } from "/static/util.js";

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

export function createStateGraph(container, { graphUrl, statsUrl }) {
  const box = el("div", { class: "stategraph" });
  container.append(box);
  let states = [];
  let phase = "";
  let stats = {};        // norm(phase) → {turns, tokens, cost, elapsed_s}
  let statsLoaded = false;  // only a LOADED stats map can prove a state was skipped
  let unphased = null;   // turns from before the run entered any stage

  function statsLine(st) {
    const parts = [`${st.turns} turn${st.turns === 1 ? "" : "s"}`];
    if (st.tokens) parts.push(`${fmtNum(st.tokens)} tok`);
    if (st.elapsed_s) parts.push(fmtDur(st.elapsed_s));
    const cost = fmtCost({ cost: st.cost });
    if (cost) parts.push(cost);
    return parts.join(" · ");
  }

  function renderInto() {
    box.replaceChildren();
    if (!states.length && !phase) {
      box.append(el("div", { class: "faint small" }, "no state graph — this routine has no stage modules"));
      return;
    }
    const cur = norm(phase);
    let idx = states.findIndex((s) => norm(s.name) === cur);
    const rows = [...states];
    if (phase && idx < 0) {          // a phase outside the parsed flow — show it anyway
      rows.push({ name: phase, desc: "(recorded phase — not one of the stage modules)" });
      idx = rows.length - 1;
    }
    // Skip detection needs proof the run stamps phases at all — a diagram highlighted
    // synthetically (a conversation's reply cycle) must not read as skipped work.
    const anyPhased = statsLoaded && Object.keys(stats).length > 0;
    rows.forEach((s, i) => {
      const st = stats[norm(s.name)];
      // a phase before the current one with no recorded turns was never entered —
      // the run jumped over its module (every visited phase stamps ≥1 turn)
      const skipped = anyPhased && phase && i < idx && !st;
      const cls = !phase ? "todo" : skipped ? "done skipped"
        : i < idx ? "done" : i === idx ? "current" : "todo";
      box.append(el("div", { class: `sg-node ${cls}`,
        title: skipped ? "skipped — the run recorded no turns in this phase" : s.desc || s.name },
        el("span", { class: "sg-dot" }, i === idx && phase ? "●"
          : skipped ? "»" : i < idx && phase ? "✓" : "○"),
        el("span", { class: "sg-name" }, s.name),
        st ? el("span", { class: "sg-stats", title: "this phase so far: turns · tokens · wall-clock · cost" },
          statsLine(st))
           : skipped ? el("span", { class: "sg-stats" }, "skipped") : null));
      if (i < rows.length - 1) box.append(el("div", { class: `sg-link ${i < idx ? "done" : ""}` }));
    });
    if (unphased?.turns) {
      box.append(el("div", { class: "sg-foot" }, `before any phase: ${statsLine(unphased)}`));
    }
  }

  async function refreshStats() {
    if (!statsUrl) return;
    try {
      const r = await api(statsUrl);
      stats = {};
      unphased = null;
      for (const p of r.phases || []) {
        if (p.phase) stats[norm(p.phase)] = p;
        else unphased = p;
      }
      statsLoaded = true;
    } catch { return; /* instrumentation is decoration — never break the diagram */ }
    renderInto();
  }

  async function refresh() {
    try {
      const g = await api(graphUrl);
      states = g.states || [];
      phase = g.current || "";
    } catch { states = []; }
    renderInto();
    refreshStats();
  }

  refresh();
  return {
    refresh,
    setPhase(p) {                      // live: an SSE state event carried a new phase
      if (p == null || p === phase) return;
      phase = p;
      renderInto();
      refreshStats();                  // a transition moved spend/timing — refresh numbers
    },
  };
}
