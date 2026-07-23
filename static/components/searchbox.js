// Global header search — instance-wide full text over runs, decisions, memory, ledgers,
// and recipes (GET /api/search). Results drop down under the topbar input, grouped by
// routine/conversation, and every hit deep-links into the run / conversation / decisions
// view. "/" or Ctrl-K focuses the box from anywhere; arrows + Enter drive the list.
// Snippets arrive with private-use sentinels around matches (the server's MARK_START/END);
// they become <mark> nodes via textContent only — no HTML rides the payload.

import { api } from "/static/api.js";
import { el, fmtTs } from "/static/util.js";

const MARK_START = "\ue000";
const MARK_END = "\ue001";
const MIN_CHARS = 2;
const DEBOUNCE_MS = 250;
const LIMIT = 60;

// Where a hit leads: transcript-ish kinds carry a run_ts → the run view (subrun hits keep
// their sub path in the query, which the run view's transcript pager understands);
// a conversation's surface is the chat; durable decision records live on the Decisions tab;
// routine-level files (ledger/memory/recipe/instruction) land on the routine page.
export function hitHref(h) {
  if (h.kind === "decision") return "#/questions";
  if (h.home === "conversation") return `#/conversations/${h.slug}`;
  if (h.run_ts) {
    const base = `#/run/${h.slug}:${h.run_ts}`;
    return h.sub ? `${base}?sub=${encodeURIComponent(h.sub)}` : base;
  }
  return `#/routine/${h.slug}`;
}

// match → <mark>match</mark>, built with textContent only.
export function markSnippet(snippet) {
  const out = el("span", { class: "gs-snip" });
  String(snippet || "").split(MARK_START).forEach((part, i) => {
    if (i === 0) { if (part) out.append(part); return; }
    const end = part.indexOf(MARK_END);
    if (end === -1) { out.append(part); return; }
    out.append(el("mark", {}, part.slice(0, end)));
    const rest = part.slice(end + MARK_END.length);
    if (rest) out.append(rest);
  });
  return out;
}

function hitMeta(h) {
  const bits = [];
  if (h.run_ts) bits.push(`run ${fmtTs(h.run_ts)}`);
  if (h.sub) bits.push(`sub ${h.sub}`);
  if (h.turn !== null && h.turn !== undefined) bits.push(`turn ${h.turn}`);
  if (h.phase) bits.push(h.phase);
  return bits.join(" · ");
}

function groupHits(hits) {
  const groups = new Map();   // "home:slug" → {home, slug, hits[]} in first-hit rank order
  for (const h of hits) {
    const key = `${h.home}:${h.slug}`;
    if (!groups.has(key)) groups.set(key, { home: h.home, slug: h.slug, hits: [] });
    groups.get(key).hits.push(h);
  }
  return [...groups.values()];
}

export function initSearchBox() {
  const slot = document.getElementById("global-search");
  if (!slot) return;
  // the resting slot is icon-sized on desktop (the input expands over the nav on focus),
  // full-row on mobile — the placeholder only has room once focused / on the wide slot
  const REST_HINT = "search";
  const FOCUS_HINT = "search runs, decisions, notes…";
  const input = el("input", {
    class: "gs-input", type: "search", autocomplete: "off", spellcheck: "false",
    placeholder: REST_HINT, title: "search everything ( / or Ctrl-K )",
    "aria-label": "search everything", role: "combobox", "aria-autocomplete": "list",
    "aria-expanded": "false", "aria-controls": "gs-pop",
  });
  const pop = el("div", { class: "gs-pop", id: "gs-pop", role: "listbox", hidden: true });
  slot.classList.add("gsearch");
  slot.append(input, pop);

  let seq = 0;          // stale-response guard (no AbortController in api())
  let timer = null;
  let active = -1;      // index into the flat hit-row list for keyboard driving

  const rows = () => [...pop.querySelectorAll(".gs-hit")];

  function close() {
    pop.hidden = true;
    pop.replaceChildren();
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function setActive(i) {
    const list = rows();
    if (!list.length) return;
    active = (i + list.length) % list.length;
    list.forEach((r, n) => {
      r.classList.toggle("active", n === active);
      r.setAttribute("aria-selected", n === active ? "true" : "false");
    });
    input.setAttribute("aria-activedescendant", list[active].id);
    list[active].scrollIntoView({ block: "nearest" });
  }

  function render(data) {
    pop.replaceChildren();
    const groups = groupHits(data.hits || []);
    if (!groups.length) {
      pop.append(el("div", { class: "gs-empty" }, "no matches"));
    }
    for (const g of groups) {
      pop.append(el("div", { class: "gs-group" },
        el("span", { class: `chip ${g.home === "conversation" ? "" : "ok"}` }, g.home),
        el("b", {}, g.slug)));
      for (const h of g.hits) {
        const row = el("a", { class: "gs-hit", href: hitHref(h), tabindex: "-1",
          role: "option", id: `gs-opt-${rows().length}`, "aria-selected": "false" },
          el("span", { class: "gs-kind" }, h.kind),
          markSnippet(h.snippet),
          el("span", { class: "gs-meta" }, hitMeta(h)));
        row.addEventListener("click", () => close());
        pop.append(row);
      }
    }
    if (data.index && data.index.pending > 0) {
      pop.append(el("div", { class: "gs-note" },
        `still indexing — ${data.index.pending} file(s) pending, results may be incomplete`));
    }
    pop.hidden = false;
    input.setAttribute("aria-expanded", "true");
    active = -1;
    input.removeAttribute("aria-activedescendant");
  }

  async function run() {
    const q = input.value.trim();
    if (q.length < MIN_CHARS) { close(); return; }
    const my = ++seq;
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(q)}&limit=${LIMIT}`);
      if (my !== seq || input.value.trim() !== q) return;   // superseded while in flight
      render(data);
    } catch (err) {
      if (my !== seq) return;
      pop.replaceChildren(el("div", { class: "gs-empty" }, err.message));
      pop.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(run, DEBOUNCE_MS);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { close(); input.blur(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
    else if (e.key === "Enter") {
      const list = rows();
      const target = list[active] || list[0];
      if (target) { target.click(); location.hash = target.getAttribute("href"); }
    }
  });
  input.addEventListener("focus", () => {
    input.placeholder = FOCUS_HINT;
    if (input.value.trim().length >= MIN_CHARS) run();
  });
  input.addEventListener("blur", () => { input.placeholder = REST_HINT; });

  // "/" (outside inputs) or Ctrl/Cmd-K from anywhere jumps to search.
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "")
      || document.activeElement?.isContentEditable;
    const ctrlK = (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k";
    if (ctrlK || (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey)) {
      e.preventDefault();
      input.focus();
      input.select();
    }
  });
  document.addEventListener("click", (e) => { if (!slot.contains(e.target)) close(); });
  window.addEventListener("hashchange", close);
}
