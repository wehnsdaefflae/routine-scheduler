// Dashboard: routine cards with status, next fire, last outcome, run-now.

import { api } from "/static/api.js";
import { chip, el, fmtTs, relTime, toast } from "/static/util.js";

export async function render(view) {
  view.append(el("h1", {}, "Routines"));
  const grid = el("div", { class: "grid" });
  view.append(grid);

  async function load() {
    const cards = await api("/api/routines");
    grid.innerHTML = "";
    if (!cards.length) {
      grid.append(el("div", { class: "empty" },
        "No routines yet — create the first one with “+ New routine”."));
      return;
    }
    for (const c of cards) grid.append(card(c));
  }

  function card(c) {
    const state = c.active_state || (c.enabled ? "idle" : "disabled");
    const stateChip = c.active_state ? chip(c.active_state, c.active_state)
      : c.enabled ? chip("idle") : chip("disabled", "disabled");
    const last = c.last_run;
    return el("div", { class: "card" },
      el("div", { class: "title" },
        el("a", { href: `#/routine/${c.slug}` }, c.name || c.slug),
        stateChip),
      el("div", { class: "meta" },
        el("span", {}, c.cron ? `⏱ ${c.cron} (${c.tz})` : "no schedule"),
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
