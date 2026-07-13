// A routine's state graph as a simple highlighted chain: one node per state parsed from
// the routine's own main.md (server-side, /stategraph), the CURRENT phase lit up, states
// before it dimmed as done. setPhase() re-highlights live — the run SSE `state` events
// carry phase transitions (the engine mirrors state/phase.json writes into status.json).
// A recorded phase that matches no parsed state is appended as its own node: the diagram
// never lies about where the run says it is.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

export function createStateGraph(container, { graphUrl }) {
  const box = el("div", { class: "stategraph" });
  container.append(box);
  let states = [];
  let phase = "";

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
      box.append(el("div", { class: `sg-node ${cls}`, title: s.desc || s.name },
        el("span", { class: "sg-dot" }, i === idx && phase ? "●" : i < idx && phase ? "✓" : "○"),
        el("span", { class: "sg-name" }, s.name)));
      if (i < rows.length - 1) box.append(el("div", { class: `sg-link ${i < idx ? "done" : ""}` }));
    });
  }

  async function refresh() {
    try {
      const g = await api(graphUrl);
      states = g.states || [];
      phase = g.current || "";
    } catch { states = []; }
    renderInto();
  }

  refresh();
  return {
    refresh,
    setPhase(p) {                      // live: an SSE state event carried a new phase
      if (p == null || p === phase) return;
      phase = p;
      renderInto();
    },
  };
}
