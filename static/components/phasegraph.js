// A routine's PHASE graph as a simple highlighted chain: one node per stage module
// (server-side, /phasegraph — the modules ARE the phases, nothing parsed from prose),
// the CURRENT phase lit up, phases before it dimmed as done. setPhase() re-highlights
// live — the run tail's `state` events carry phase transitions (the engine stamps the
// stage module each read_file enters into status.json).
// NAMING (D193): the concept is a PHASE everywhere — the engine's persisted key, the API
// field, this component. `stages/` is the recipe's FILE layout the nodes come from, and
// `state` keeps its unrelated meanings (run state, the `state/` dir). CLAUDE.md holds the
// one rule under "Phase, stage, state".
// A recorded phase that matches no stage module is appended as its own node: the diagram
// never lies about where the run says it is. With a `statsUrl` (/api/runs/…/phases) each
// visited node also shows its instrumentation — turns · tokens · wall-clock · cost —
// refreshed on every phase transition: the rail is the run's instrument panel. A phase
// BEFORE the current one that no turn ever ran under (the run never read its module)
// is marked "skipped" rather than checked off — passed, not done.
// A node whose phase HAS turns is a LINK to where that phase begins in the transcript
// (statemap's `first_turn`): `turnHref(n)` from the host view builds the address, and the
// run view lands and flashes the turn box. A phase with no recorded turn has nowhere to
// go, so it stays a plain node rather than a link that dead-ends.

import { api } from "/static/api.js";
import { el, fmtCost, fmtDur, fmtNum } from "/static/util.js";

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

export function createPhaseGraph(container, { graphUrl, statsUrl, turnHref }) {
  const box = el("div", { class: "phasegraph" });
  container.append(box);
  let phases = [];
  let phase = "";
  let loadError = "";    // graph fetch failure — distinct from "genuinely no stages"
  let stats = {};        // norm(phase) → {turns, tokens, cost, elapsed_s, first_turn}
  let statsLoaded = false;  // only a LOADED stats map can prove a phase was skipped
  let unphased = null;   // turns from before the run entered any stage

  function statsLine(st) {
    const parts = [`${st.turns} turn${st.turns === 1 ? "" : "s"}`];
    if (st.tokens) parts.push(`${fmtNum(st.tokens)} tok`);
    if (st.elapsed_s) parts.push(fmtDur(st.elapsed_s));
    const cost = fmtCost({ cost: st.cost });
    if (cost) parts.push(cost);
    return parts.join(" · ");
  }

  // The node itself: an <a> when there is a turn to land on, a <div> otherwise. Same class
  // and same children either way, so the stylesheet and the browser suite see one node.
  function nodeEl(attrs, ...kids) {
    const turn = attrs.firstTurn;
    const href = turn != null && turnHref ? turnHref(turn) : null;
    delete attrs.firstTurn;
    if (!href) return el("div", attrs, ...kids);
    return el("a", { ...attrs, href, "data-turn-link": String(turn),
      title: `${attrs.title || ""} — go to turn ${turn}, where this phase begins`.trim() }, ...kids);
  }

  function renderInto() {
    box.replaceChildren();
    if (!phases.length && !phase) {
      box.append(el("div", { class: "faint small" }, loadError
        ? `phase graph unavailable — ${loadError}`
        : "no phase graph — this routine has no stage modules"));
      return;
    }
    const cur = norm(phase);
    let idx = phases.findIndex((s) => norm(s.name) === cur);
    const rows = [...phases];
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
      box.append(nodeEl({ class: `pg-node ${cls}`, firstTurn: st ? st.first_turn : null,
        title: skipped ? "skipped — the run recorded no turns in this phase" : s.desc || s.name },
        el("span", { class: "pg-dot" }, i === idx && phase ? "●"
          : skipped ? "»" : i < idx && phase ? "✓" : "○"),
        el("span", { class: "pg-name" }, s.name),
        st ? el("span", { class: "pg-stats", title: "this phase so far: turns · tokens · wall-clock · cost" },
          statsLine(st))
           : skipped ? el("span", { class: "pg-stats" }, "skipped") : null));
      if (i < rows.length - 1) box.append(el("div", { class: `pg-link ${i < idx ? "done" : ""}` }));
    });
    if (unphased?.turns) {
      box.append(el("div", { class: "pg-foot" }, `before any phase: ${statsLine(unphased)}`));
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
      phases = g.phases || [];
      phase = g.current || "";
      loadError = "";
    } catch (err) { phases = []; loadError = err.message || "fetch failed"; }
    renderInto();
    refreshStats();
  }

  refresh();
  return {
    refresh,
    setPhase(p) {                      // live: a stream state event carried a new phase
      if (p == null || p === phase) return;
      phase = p;
      renderInto();
      refreshStats();                  // a transition moved spend/timing — refresh numbers
    },
  };
}
