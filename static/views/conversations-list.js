// The conversations SIDEBAR: the list read, its search/tag filter, its rows and the one shared
// hover card. Everything about *which* conversations exist; nothing about the one being read.
//
// Extracted from `views/conversations.js` (D307's second commit), and the reason is worth
// keeping: that file sat AT its size-ratchet ceiling with zero slack, so a four-line change
// could not land without paying for itself somewhere. I paid twice by tightening comments, and
// the operator stopped it — *"how long do you wanna squeeze one file instead of just
// splitting?!"* He is right: shaving prose to buy lines leaves the file exactly as full as it
// was, so the next change pays the same tax, and the comments that go are the ones explaining
// the hardest parts. The sidebar is the natural seam — it shares only `slug` (which row is
// current) and a `view` to hang the hover card on, and it is read and changed on its own.
//
// WHAT STAYS BEHIND, deliberately: `loadList` is called by the center pane too (a rename, a tag
// edit and the branch family all want fresh rows), so the module hands its reload back rather
// than owning when it happens.

import { el, emptyState, relTime } from "/static/util.js";
import { api } from "/static/api.js";

/**
 * Mount the sidebar into `host` and return what the view needs back.
 *
 * `onCurrent` names the slug being read, as a function rather than a value: the view remounts
 * on navigation without rebuilding the sidebar, and the "on" row has to follow.
 */
export function createConversationList(host, { view, onCurrent }) {
  const sideList = el("div", { class: "conv-list" });
  const tagSel = el("select", { class: "conv-tagsel", "data-nopersist": "",
    title: "filter by tag" });
  const search = el("input", { type: "search", placeholder: "search…" });
  const newBtn = el("a", { class: "btn primary small", href: "#/conversations" }, "+ new");
  host.append(el("div", { class: "row", style: "gap:6px" }, search, newBtn),
              el("div", { class: "row", style: "gap:6px" }, tagSel), sideList);

  let items = [], activeTag = "";

  // The list reloads on a 20 s timer, on run events and after a rename or a tag edit — reads
  // that overlap and answer out of order. Only the newest one paints: a read begun before a
  // rename used to land after it and put the old title back.
  let listSeq = 0;

  // One shared hover card: the list rows stay one line each; snippet, tags, and state detail
  // appear beside the row on hover instead of costing permanent vertical space.
  let hover = null, hoverHideT = 0;
  const hideHover = () => { clearTimeout(hoverHideT); if (hover) hover.hidden = true; };

  function showHover(row, it) {
    if (!hover) {
      hover = el("div", { class: "conv-hover", hidden: true });
      view.append(hover);
    }
    clearTimeout(hoverHideT);
    const when = it.updated ? relTime(it.updated) : "no replies yet";
    hover.replaceChildren(...[
      el("div", { class: "conv-hover-title" }, it.title || it.slug),
      el("div", { class: "faint small" },
        `${it.state} · ${when}${it.turns ? ` · ${it.turns} turns` : ""}`),
      it.snippet ? el("div", { class: "conv-hover-snippet" }, it.snippet) : null,
      it.tags?.length ? el("div", { class: "conv-hover-tags" },
        ...it.tags.map((t) => el("span", { class: "tag" }, t))) : null,
      it.question ? el("div", { class: "small", style: "color:var(--warn)" },
        "❓ waiting for you") : null,
    ].filter(Boolean));
    hover.hidden = false;
    const r = row.getBoundingClientRect();
    hover.style.left = Math.min(r.right + 8, window.innerWidth - 300) + "px";
    hover.style.top = Math.min(r.top, window.innerHeight - hover.offsetHeight - 12) + "px";
  }

  function renderList() {
    const current = onCurrent();
    const q = search.value.trim().toLowerCase();
    const tags = [...new Set(items.flatMap((i) => i.tags || []))].sort();
    tagSel.replaceChildren(
      el("option", { value: "" }, "all tags"),
      ...tags.map((t) => el("option",
        { value: t, ...(t === activeTag ? { selected: true } : {}) }, t)));
    tagSel.hidden = !tags.length;
    const shown = items.filter((i) =>
      (!activeTag || (i.tags || []).includes(activeTag))
      && (!q || `${i.title} ${i.snippet} ${(i.tags || []).join(" ")}`.toLowerCase().includes(q)));
    sideList.replaceChildren();
    hideHover();
    if (!shown.length) {
      sideList.append(emptyState("💬", items.length ? "No matches" : "No conversations yet",
        items.length ? "" : "Start one — the first message is the task."));
      return;
    }
    for (const it of shown) {
      const row = el("a", {
        class: `conv-item${it.slug === current ? " on" : ""}`,
        href: `#/conversations/${it.slug}` },
        el("span", { class: `dot ${it.state}`, role: "img", title: it.state,
          "aria-label": `state: ${it.state}` }),
        el("span", { class: "conv-title" }, it.title || it.slug),
        it.question ? el("span", { class: "conv-q", title: "waiting for you" }, "❓") : null,
        el("span", { class: "conv-when" }, relTime(it.updated)));
      row.addEventListener("mouseenter", () => showHover(row, it));
      row.addEventListener("mouseleave",
        () => { hoverHideT = setTimeout(() => { if (hover) hover.hidden = true; }, 120); });
      sideList.append(row);
    }
  }

  async function loadList() {
    const seq = ++listSeq;
    let next;
    try { next = await api("/api/conversations"); }
    catch (err) {
      if (seq !== listSeq) return;
      const retry = el("button", { class: "btn small", onclick: loadList }, "retry");
      sideList.replaceChildren(el("div", { class: "empty" },
        el("div", {}, `couldn't load conversations: ${err.message}`), retry));
      return;
    }
    if (seq !== listSeq) return;
    items = next;
    renderList();
  }

  search.oninput = renderList;
  tagSel.onchange = () => { activeTag = tagSel.value; renderList(); };

  return {
    /** Re-read and repaint. The center pane calls it after a rename or a tag edit. */
    loadList,
    /** Repaint from what is already loaded — for a navigation that only moves the "on" row. */
    renderList,
    /** The rows as last read: the head's branch family is derived from them (D306 pt 5). */
    rows: () => items,
    /** Hide the hover card — the view's teardown, so it cannot outlive its rows. */
    hideHover,
  };
}
