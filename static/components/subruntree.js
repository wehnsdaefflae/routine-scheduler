// The recursive sub-run tree: a run's SEQUENTIAL subtasks (→) and PARALLEL subruns (⇉), each a
// node with a state icon, its workflow pattern, and a per-node turn-budget meter (amber ≥85%,
// red over). Fed by the read-model at /api/runs/<id>/tree (a walk of the on-disk sub/ tree).
// Refreshes on demand (the run view calls refresh() on live-stream state events) and polls itself while
// the run is live — the sibling of the phase-graph rail card, but for within-run decomposition.
// Each row links to the parent turn that started the child (components/turnlink.js, D193).

import { api } from "/static/api.js";
import { el } from "/static/util.js";

const STATE_ICON = { running: "◐", ok: "●", partial: "◑", failed: "✕", aborted: "⊘" };
// `oversight` is the escalation ladder's rung (engine/child.OVERSIGHT): a child that supervises
// the run that started it, drawn pointing UP because it reports on its parent rather than for it.
// Without the entry a rung fell through to the "•" unknown-mode default — the tree showed a child
// nobody could name.
const MODE_ICON = { sequential: "→", parallel: "⇉", oversight: "↑" };

export function createSubRunTree(container, { treeUrl, isLive, turnHref }) {
  const box = el("div", { class: "subruntree" });
  container.append(box);
  let timer = null;

  // The row is a LINK to where the child begins in the PARENT's transcript (D193):
  // read-model `start_turn`, addressed through components/turnlink.js. A node with no
  // recorded start turn (a nested child, a resumed run's re-attached one) stays a plain
  // row — a link that lands nowhere is worse than none.
  function rowEl(n, attrs, ...kids) {
    const href = n.start_turn != null && turnHref ? turnHref(n.start_turn) : null;
    if (!href) return el("div", attrs, ...kids);
    return el("a", { ...attrs, href, "data-turn-link": String(n.start_turn),
      title: `${attrs.title || ""}\ngo to turn ${n.start_turn}, where this child was started`
        .trim() }, ...kids);
  }

  function node(n) {
    const st = n.state || "running";
    const kids = [
      el("span", { class: "sr-icon", title: st }, STATE_ICON[st] || "○"),
      el("span", { class: `sr-mode sr-${n.mode}`, title: n.mode + " child" }, MODE_ICON[n.mode] || "•"),
      el("span", { class: "sr-label" }, `${n.n}. ${n.label || "(child)"}`),
    ];
    if (n.workflow) kids.push(el("span", { class: "sr-wf faint small" }, n.workflow));
    const budget = n.budget && n.budget.turns > 0 ? n.budget.turns : 0;
    const used = n.turns || 0;
    if (budget) {
      const frac = Math.min(1, used / budget);
      const cls = used > budget ? "over" : frac >= 0.85 ? "warn" : "";
      kids.push(el("span", { class: "sr-meter", title: `${used} / ${budget} turns` },
        el("span", { class: `sr-bar ${cls}`, style: `width:${Math.round(frac * 100)}%` })));
      kids.push(el("span", { class: "sr-turns faint small" }, `${used}/${budget}`));
    } else {
      kids.push(el("span", { class: "sr-turns faint small" }, `${used}t`));
    }
    const row = rowEl(n, { class: `sr-row sr-s-${st}`, title: n.summary || "" }, ...kids);
    const wrap = el("div", { class: "sr-node" }, row);
    if (n.children && n.children.length) {
      const sub = el("div", { class: "sr-children" });
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
        el("div", { class: "faint small" }, `sub-run tree unavailable — ${err.message}`));
    }
  }

  // The poller is SELF-DEFENDING (weekgrid.js, transcript.js hold the same guard): a view that
  // forgets stop() would otherwise leave a 3s GET running for the rest of the browser session,
  // because isLive() reads a state variable frozen at whatever it held when the view was torn
  // down — the run view did exactly that until 0.365.0. Once the box has left the document
  // nothing can render the answer, so nothing asks for it.
  // `stopped` is what makes stop() final: clearing the timer alone did not hold against a
  // refresh already in flight — the first one's `.then(poll)` re-armed the poll after a stop()
  // that landed before it resolved (the run view's "run not found" path does exactly that).
  let stopped = false;
  function poll() {
    if (timer) clearTimeout(timer);
    timer = null;
    if (stopped || !box.isConnected) return;
    if (isLive && isLive()) timer = setTimeout(async () => {
      if (stopped || !box.isConnected) return;
      await refresh();
      poll();
    }, 3000);
  }

  refresh().then(poll);
  return {
    refresh() { if (!stopped) { refresh(); poll(); } },
    stop() { stopped = true; if (timer) clearTimeout(timer); timer = null; },
  };
}
