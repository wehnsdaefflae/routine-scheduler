// The recursive task tree: a run's SEQUENTIAL subtasks (→) and PARALLEL subruns (⇉), each a
// node with a state icon, its workflow pattern, and a per-node turn-budget meter (amber ≥85%,
// red over). Fed by the read-model at /api/runs/<id>/tree (a walk of the on-disk sub/ tree).
// Refreshes on demand (the run view calls refresh() on SSE state events) and polls itself while
// the run is live — the sibling of the state-graph rail card, but for within-run decomposition.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

const STATE_ICON = { running: "◐", ok: "●", partial: "◑", failed: "✕", aborted: "⊘" };
const MODE_ICON = { sequential: "→", parallel: "⇉" };

export function createTaskTree(container, { treeUrl, isLive }) {
  const box = el("div", { class: "tasktree" });
  container.append(box);
  let timer = null;

  function node(n) {
    const st = n.state || "running";
    const kids = [
      el("span", { class: "tt-icon", title: st }, STATE_ICON[st] || "○"),
      el("span", { class: `tt-mode tt-${n.mode}`, title: n.mode + " child" }, MODE_ICON[n.mode] || "•"),
      el("span", { class: "tt-label" }, `${n.n}. ${n.label || "(child)"}`),
    ];
    if (n.workflow) kids.push(el("span", { class: "tt-wf faint small" }, n.workflow));
    const budget = n.budget && n.budget.turns > 0 ? n.budget.turns : 0;
    const used = n.turns || 0;
    if (budget) {
      const frac = Math.min(1, used / budget);
      const cls = used > budget ? "over" : frac >= 0.85 ? "warn" : "";
      kids.push(el("span", { class: "tt-meter", title: `${used} / ${budget} turns` },
        el("span", { class: `tt-bar ${cls}`, style: `width:${Math.round(frac * 100)}%` })));
      kids.push(el("span", { class: "tt-turns faint small" }, `${used}/${budget}`));
    } else {
      kids.push(el("span", { class: "tt-turns faint small" }, `${used}t`));
    }
    const row = el("div", { class: `tt-row tt-s-${st}`, title: n.summary || "" }, ...kids);
    const wrap = el("div", { class: "tt-node" }, row);
    if (n.children && n.children.length) {
      const sub = el("div", { class: "tt-children" });
      n.children.forEach((c) => sub.append(node(c)));
      wrap.append(sub);
    }
    return wrap;
  }

  function renderInto(tree) {
    box.replaceChildren();
    if (!tree || !tree.length) {
      box.append(el("div", { class: "faint small" }, "no subtasks yet — appears when the run decomposes"));
      return;
    }
    tree.forEach((n) => box.append(node(n)));
  }

  let loadedOnce = false;
  async function refresh() {
    try {
      const r = await api(treeUrl);
      renderInto(r.tree || []);
      loadedOnce = true;
    } catch (err) {
      // transient refresh errors keep the last render; a FIRST load failing must
      // not read as "no subtasks yet"
      if (!loadedOnce) box.replaceChildren(
        el("div", { class: "faint small" }, `subtask tree unavailable — ${err.message}`));
    }
  }

  function poll() {
    if (timer) clearTimeout(timer);
    if (isLive && isLive()) timer = setTimeout(async () => { await refresh(); poll(); }, 3000);
  }

  refresh().then(poll);
  return {
    refresh() { refresh(); poll(); },
    stop() { if (timer) clearTimeout(timer); },
  };
}
