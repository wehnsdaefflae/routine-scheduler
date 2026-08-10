// Summary/overview: the last thing each routine told you — its most recent run's finish
// message — in ONE glance surface, most-recently-updated first. A sibling to the Decisions
// inbox (which collects what the routines need FROM you); this collects what they last SAID.
// Each row links to the run and can be dismissed (marked read); a newer run of that routine
// resurfaces it automatically. Backed by /api/summary (registry read-model + a small
// per-routine read-marker under .control/).

import { api } from "/static/api.js";
import { md } from "/static/md.js";
import { chip, el, emptyState, skeleton, toast, when } from "/static/util.js";

const FILTERS = [["all", "All"], ["unread", "Unread"]];

export async function render(view, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / summary"),
      el("h1", {}, "Summary"),
      el("div", { class: "sub" }, "the latest finish message from every routine — newest first"))));

  // Unread is the default (operator, 2026-08-05): the point of Summary is what you have NOT
  // yet seen, so open on it unless the URL explicitly asks for all.
  const state = { filter: query.filter === "all" ? "all" : "unread", items: [] };

  const filterChips = new Map();
  const chipRow = el("div", { class: "row", style: "gap:6px" });
  for (const [key, label] of FILTERS) {
    const b = el("button", { class: "btn small" }, label);
    b.onclick = () => { state.filter = key; syncToolbar(); renderList(); };
    filterChips.set(key, b);
    chipRow.append(b);
  }
  // Bulk dismiss (F303): the traces showed the operator clearing the inbox one click per
  // row (6–9 rapid "mark read" clicks in a burst) — one button does the sweep. It reuses
  // the per-routine endpoint row-by-row, so read-marker semantics stay identical.
  const allReadBtn = el("button", { class: "btn small", "data-summary-mark-all": "" },
    "✓ mark all read");
  allReadBtn.onclick = async () => {
    const unread = state.items.filter((r) => !r.read);
    if (!unread.length) return;
    allReadBtn.disabled = true;
    try {
      await Promise.all(unread.map((r) =>
        api(`/api/summary/${r.slug}/read`, { method: "POST",
          body: { run_id: r.run_id, read: true } })));
      for (const r of unread) r.read = true;
      toast(`${unread.length} marked read`);
    } catch (err) { toast(err.message, 4000, { error: true }); }
    syncToolbar(); renderList();
  };
  view.append(el("div", { class: "row mt toolbar", style: "gap:10px" }, chipRow, allReadBtn));

  const list = el("div", { class: "mt" });
  list.append(skeleton(), skeleton());
  view.append(list);

  function syncToolbar() {
    for (const [key, b] of filterChips) b.classList.toggle("active", key === state.filter);
    allReadBtn.disabled = !state.items.some((r) => !r.read);
  }

  function visible() {
    return state.filter === "unread" ? state.items.filter((r) => !r.read) : state.items;
  }

  function row(r) {
    const outcomeChip = r.outcome ? chip(r.outcome, r.outcome)
      : (r.state ? chip(r.state, r.state) : null);
    const markBtn = el("button", { class: "btn small" }, r.read ? "mark unread" : "mark read");
    markBtn.onclick = async () => {
      markBtn.disabled = true;
      const next = !r.read;
      try {
        await api(`/api/summary/${r.slug}/read`, { method: "POST",
          body: { run_id: r.run_id, read: next } });
        r.read = next;
        renderList();
      } catch (err) { toast(err.message, 4000, { error: true }); markBtn.disabled = false; }
    };
    return el("div", { class: `panel summary-item${r.read ? " read" : ""}` },
      el("div", { class: "q-meta" },
        el("a", { class: "b", href: `#/routine/${r.slug}` }, r.title || r.slug),
        outcomeChip,
        r.updated || r.ts ? el("span", { class: "faint small" }, when(r.updated || r.ts)) : null,
        !r.read ? chip("unread", "meta") : null),
      md(r.summary || "_(no finish summary yet)_"),
      el("div", { class: "row mt", style: "gap:8px" },
        el("a", { class: "btn small", href: `#/run/${r.run_id}` }, "view run"),
        markBtn));
  }

  function renderList() {
    const rows = visible();
    list.replaceChildren();
    if (!rows.length) {
      list.append(emptyState("✓", state.filter === "unread" ? "Nothing unread" : "No runs yet",
        state.filter === "unread" ? "every routine's latest message has been read"
                                  : "routines will show their latest finish message here once they run"));
      return;
    }
    for (const r of rows) list.append(row(r));
  }

  async function load() {
    try { state.items = await api("/api/summary"); }
    catch (err) { list.replaceChildren(emptyState("✕", "Couldn't load summary", err.message)); return; }
    syncToolbar(); renderList();
  }

  syncToolbar();
  await load();
  const onBus = (e) => { if (e.detail?.event === "run_finished") load(); };
  window.addEventListener("rsched-bus", onBus);
  return () => window.removeEventListener("rsched-bus", onBus);
}
