// Dashboard: routine cards with status, next fire, last outcome, run-now.
// A tag filter bar sits on top: meta routines are tucked away by default; click tags to filter.

import { api } from "/static/api.js";
import { chip, el, fmtTs, relTime, tagChip, toast } from "/static/util.js";

const FILTER_KEY = "rsched_dash_tags";

export async function render(view) {
  view.append(el("h1", {}, "Routines"));
  const banner = el("div", {});
  const filterBar = el("div", { class: "filterbar" });
  const grid = el("div", { class: "grid" });
  view.append(banner, filterBar, grid);

  let cards = [], llmReady = true;
  const active = new Set(JSON.parse(localStorage.getItem(FILTER_KEY) || "[]"));

  function visible(c) {
    const tags = c.tags || [];
    if (!active.size) return !tags.includes("meta");   // default view tucks meta away
    return tags.some((t) => active.has(t));
  }

  function renderFilterBar() {
    const all = [...new Set(cards.flatMap((c) => c.tags || []))]
      .sort((a, b) => (a === "meta" ? -1 : b === "meta" ? 1 : a.localeCompare(b)));
    filterBar.innerHTML = "";
    if (!all.length) return;
    filterBar.append(el("span", { class: "lbl" }, "filter"));
    for (const t of all) {
      filterBar.append(tagChip(t, {
        active: active.has(t),
        onClick: () => {
          active.has(t) ? active.delete(t) : active.add(t);
          localStorage.setItem(FILTER_KEY, JSON.stringify([...active]));
          renderFilterBar(); renderGrid();
        },
      }));
    }
    if (active.size) filterBar.append(el("a", {
      href: "#", class: "muted", style: "font-family:var(--mono);font-size:11px",
      onclick: (e) => { e.preventDefault(); active.clear(); localStorage.setItem(FILTER_KEY, "[]"); renderFilterBar(); renderGrid(); },
    }, "clear"));
    else if (cards.some((c) => (c.tags || []).includes("meta")))
      filterBar.append(el("span", { class: "muted", style: "font-size:11.5px;font-family:var(--mono)" },
        "· meta hidden"));
  }

  function renderGrid() {
    const shown = cards.filter(visible);
    grid.innerHTML = "";
    if (!cards.length) {
      grid.append(el("div", { class: "empty" },
        "No routines yet — create the first one with “+ New routine”."));
      return;
    }
    if (!shown.length) {
      grid.append(el("div", { class: "empty" }, "No routines match this tag filter."));
      return;
    }
    for (const c of shown) grid.append(card(c));
  }

  async function load() {
    const [routines, status] = await Promise.all([
      api("/api/routines"), api("/api/status").catch(() => ({})),
    ]);
    cards = routines;
    llmReady = status.llm_ready !== false;
    banner.innerHTML = "";
    if (!llmReady) banner.append(el("div", { class: "panel", style: "border-color:var(--warn);margin-bottom:12px" },
      el("strong", {}, "⚠ No model connected — "),
      el("span", { class: "muted" }, "add an endpoint and assign the orchestrator role in "),
      el("a", { href: "#/settings" }, "Settings"),
      el("span", { class: "muted" }, " to create or run routines.")));
    renderFilterBar();
    renderGrid();
  }

  function card(c) {
    const stateChip = c.active_state ? chip(c.active_state, c.active_state)
      : c.enabled ? chip("idle") : chip("disabled", "disabled");
    const last = c.last_run;
    return el("div", { class: "card" },
      el("div", { class: "title" },
        el("a", { href: `#/routine/${c.slug}` }, c.name || c.slug),
        stateChip),
      (c.tags || []).length ? el("div", { class: "tags" }, c.tags.map((t) => tagChip(t))) : null,
      el("div", { class: "meta" },
        el("span", {}, `⏱ ${c.schedule_desc || "Manual"}`),
        c.next_fire ? el("span", {}, `next ${relTime(c.next_fire)}`) : null,
        c.open_questions ? el("span", { class: "badge" }, `${c.open_questions} ?`) : null),
      last ? el("div", { class: "summary", title: last.summary },
        `${fmtTs(last.ts)} · ${last.state}\n${last.summary || "(no summary)"}`) :
        el("div", { class: "summary muted" }, "no runs yet"),
      c.problems?.length ? el("div", { class: "meta", style: "color:var(--err)" },
        `⚠ ${c.problems[0]}`) : null,
      el("div", { class: "actions" },
        c.active_run
          ? el("a", { class: "btn small", href: `#/run/${c.active_run}` }, "watch live")
          : el("button", {
              class: "btn small primary",
              disabled: !llmReady,
              title: llmReady ? "" : "connect an LLM endpoint in Settings first",
              onclick: async (e) => {
                e.target.disabled = true;
                try {
                  const r = await api(`/api/routines/${c.slug}/run`, { method: "POST" });
                  location.hash = `#/run/${r.run_id}`;
                } catch (err) { toast(err.message); e.target.disabled = false; }
              },
            }, "▶ run now"),
        last ? el("a", { class: "btn small", href: `#/run/${last.run_id}` }, "last run") : null));
  }

  await load();
  const onBus = () => load().catch(() => {});
  window.addEventListener("rsched-bus", onBus);
  return () => window.removeEventListener("rsched-bus", onBus);
}
