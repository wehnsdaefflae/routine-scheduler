// Dashboard: routine bays with status lamp, next fire, last outcome, open questions, run-now.
// A running routine pulses; one blocked on a question is visually loud. Meta routines are
// tucked away by default; click tags to filter.

import { api } from "/static/api.js";
import { chip, el, emptyState, skeleton, storage, tagChip, toast, when } from "/static/util.js";

const FILTER_KEY = "rsched_dash_tags";
const RUNNING = new Set(["running", "starting", "queued"]);

export async function render(view) {
  view.append(
    el("div", { class: "page-head" },
      el("div", {},
        el("div", { class: "kicker" }, "console / routines"),
        el("h1", {}, "Routines"))));
  const banner = el("div", {});
  const filterBar = el("div", { class: "filterbar" });
  const grid = el("div", { class: "grid mt" });
  view.append(banner, filterBar, grid);
  grid.append(skeleton(), skeleton(), skeleton());

  let cards = [], llmReady = true;
  const active = new Set(JSON.parse(storage.get(FILTER_KEY) || "[]"));

  function visible(c) {
    const tags = c.tags || [];
    if (!active.size) return !tags.includes("meta");   // default view tucks meta away
    return tags.some((t) => active.has(t));
  }

  function renderFilterBar() {
    const all = [...new Set(cards.flatMap((c) => c.tags || []))]
      .sort((a, b) => (a === "meta" ? -1 : b === "meta" ? 1 : a.localeCompare(b)));
    filterBar.replaceChildren();
    if (!all.length) return;
    filterBar.append(el("span", { class: "lbl" }, "filter"));
    for (const t of all) {
      filterBar.append(tagChip(t, {
        active: active.has(t),
        onClick: () => {
          active.has(t) ? active.delete(t) : active.add(t);
          storage.set(FILTER_KEY, JSON.stringify([...active]));
          renderFilterBar(); renderGrid();
        },
      }));
    }
    if (active.size) filterBar.append(el("button", { class: "btn ghost small",
      onclick: () => { active.clear(); storage.set(FILTER_KEY, "[]"); renderFilterBar(); renderGrid(); },
    }, "clear"));
    else if (cards.some((c) => (c.tags || []).includes("meta")))
      filterBar.append(el("span", { class: "faint small" }, "· meta hidden"));
  }

  function renderGrid() {
    const shown = cards.filter(visible);
    grid.replaceChildren();
    if (!cards.length) {
      grid.append(emptyState("◌", "No routines yet",
        "Create the first one with “+ new routine” — describe the task, answer a few questions, and it schedules itself."));
      return;
    }
    if (!shown.length) {
      grid.append(active.size
        ? emptyState("▢", "Nothing matches this tag filter",
            "Clear the filter above to see all routines (meta routines are hidden by default).")
        : emptyState("▢", "Only meta routines here so far",
            "Meta routines (the system's self-maintenance) are tucked away by default — click the meta tag above to show them, or create your own routine."));
      return;
    }
    for (const c of shown) grid.append(card(c));
  }

  async function load() {
    let routines, status;
    try {
      [routines, status] = await Promise.all([
        api("/api/routines"), api("/api/status").catch(() => ({})),
      ]);
    } catch (err) {
      grid.replaceChildren(emptyState("✕", "Couldn't reach the daemon", err.message));
      return;
    }
    cards = routines;
    llmReady = status.llm_ready !== false;
    banner.replaceChildren();
    if (!llmReady) banner.append(el("div", { class: "panel warn", style: "margin:12px 0" },
      el("strong", {}, "No model connected — "),
      el("span", { class: "muted" }, "add an endpoint and set the system model in "),
      el("a", { href: "#/settings" }, "Settings"),
      el("span", { class: "muted" }, " to create or run routines.")));
    renderFilterBar();
    renderGrid();
  }

  function card(c) {
    const stateChip = c.active_state ? chip(c.active_state, c.active_state)
      : c.enabled ? chip("idle", "idle") : chip("disabled", "disabled");
    const last = c.last_run;
    const blocked = c.active_state === "waiting_user";
    const cls = ["card", RUNNING.has(c.active_state) ? "live" : "", blocked ? "attention" : ""]
      .filter(Boolean).join(" ");
    return el("div", { class: cls },
      el("div", { class: "title" },
        el("a", { href: `#/routine/${c.slug}` }, c.name || c.slug),
        stateChip),
      (c.tags || []).length ? el("div", { class: "tags" }, c.tags.map((t) => tagChip(t))) : null,
      c.description ? el("div", { class: "desc" }, c.description) : null,
      blocked ? el("div", { class: "qflag" },
        el("span", {}, "waiting on your answer"),
        el("a", { class: "btn small primary", href: "#/questions", style: "margin-left:auto" }, "decide")) : null,
      el("div", { class: "meta" },
        el("span", {}, `⏱ ${c.schedule_desc || "Manual"}`),
        c.next_fire ? el("span", { title: "next scheduled fire" }, "next ", when(c.next_fire, { mode: "rel" })) : null,
        c.open_questions ? el("a", { href: "#/questions", class: "chip blocking",
          title: "open questions waiting for you" }, `${c.open_questions} open question${c.open_questions > 1 ? "s" : ""}`) : null),
      last ? el("div", { class: "lastrun" },
          el("div", { class: "lr-line" }, when(last.ts), chip(last.state, last.state)),
          el("div", { class: "lr-sum", title: last.summary || "" },
            (last.summary || "").split("\n").find((l) => l.trim()) || "(no summary)"))
        : el("div", { class: "lastrun" }, el("div", { class: "lr-sum faint" }, "never ran")),
      c.problems?.length ? el("div", { class: "problem" }, `⚠ ${c.problems[0]}`) : null,
      el("div", { class: "actions" },
        c.active_run
          ? el("a", { class: "btn small", href: `#/run/${c.active_run}` }, "◉ watch live")
          : el("button", {
              class: "btn small primary",
              disabled: !llmReady,
              title: llmReady ? "" : "connect an LLM endpoint in Settings first",
              onclick: async (e) => {
                e.target.disabled = true;
                try {
                  const r = await api(`/api/routines/${c.slug}/run`, { method: "POST" });
                  location.hash = `#/run/${r.run_id}`;
                } catch (err) { toast(err.message, 4000, { error: true }); e.target.disabled = false; }
              },
            }, "▶ run now"),
        last ? el("a", { class: "btn small", href: `#/run/${last.run_id}` }, "last run") : null));
  }

  await load();
  const onBus = () => load().catch(() => {});
  window.addEventListener("rsched-bus", onBus);
  return () => window.removeEventListener("rsched-bus", onBus);
}
