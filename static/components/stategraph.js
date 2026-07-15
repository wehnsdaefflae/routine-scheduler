// A routine's state graph as a simple highlighted chain: one node per state parsed from
// the routine's own main.md (server-side, /stategraph), the CURRENT phase lit up, states
// before it dimmed as done. setPhase() re-highlights live — the run SSE `state` events
// carry phase transitions (the engine mirrors state/phase.json writes into status.json).
// A recorded phase that matches no parsed state is appended as its own node: the diagram
// never lies about where the run says it is. With a `statsUrl` (/api/runs/…/phases) each
// visited node also shows its instrumentation — turns · tokens · wall-clock · cost —
// refreshed on every phase transition: the rail is the run's instrument panel.

import { api } from "/static/api.js";
import { el, fmtCost, fmtDur, fmtNum } from "/static/util.js";

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

export function createStateGraph(container, { graphUrl, statsUrl }) {
  const box = el("div", { class: "stategraph" });
  container.append(box);
  let states = [];
  let phase = "";
  let stats = {};        // norm(phase) → {turns, tokens, cost, elapsed_s}
  let unphased = null;   // turns from before any phase.json write

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
      box.append(el("div", { class: "faint small" }, "no state graph — main.md has no parseable run flow"));
      return;
    }
    const cur = norm(phase);
    let idx = states.findIndex((s) => norm(s.name) === cur);
    const rows = [...states];
    if (phase && idx < 0) {          // a phase outside the parsed flow — show it anyway
      rows.push({ name: phase, desc: "(recorded phase — not in main.md's run flow)" });
      idx = rows.length - 1;
    }
    rows.forEach((s, i) => {
      const cls = !phase ? "todo" : i < idx ? "done" : i === idx ? "current" : "todo";
      const st = stats[norm(s.name)];
      box.append(el("div", { class: `sg-node ${cls}`, title: s.desc || s.name },
        el("span", { class: "sg-dot" }, i === idx && phase ? "●" : i < idx && phase ? "✓" : "○"),
        el("span", { class: "sg-name" }, s.name),
        st ? el("span", { class: "sg-stats", title: "this phase so far: turns · tokens · wall-clock · cost" },
          statsLine(st)) : null));
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
