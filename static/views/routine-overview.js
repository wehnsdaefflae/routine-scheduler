// Routine overview hero + section grouping — the "informative first screen" and the
// progressive-disclosure regrouping of the config sections. The hero reads only fields the
// detail payload already carries (status, last run, recent-run heartbeat, spend, open
// decisions). groupSections reorganizes the flat <h2>+panel stream renderConfigSections and
// routine.js produce into labeled, collapsible groups WITHOUT touching any section body — the
// h2s are preserved so the side-TOC still lists them, and anything unclaimed falls into a
// trailing group so no control is ever lost.

import { heartbeat } from "/static/components/heartbeat.js";
import { chip, el, fmtDur, fmtNum, fmtUsd, when } from "/static/util.js";

function tile(label, ...body) {
  return el("div", { class: "hero-tile" },
    el("div", { class: "hero-label" }, label),
    ...body.filter(Boolean));
}

// The hero: a compact instrument band an operator reads before touching any config.
export function routineHero(d, slug) {
  const tiles = [];

  const stateName = d.active_state || (d.enabled ? "idle" : "disabled");
  tiles.push(tile("status",
    el("div", { class: "hero-strong row", style: "gap:8px;align-items:center" },
      chip(stateName, stateName),
      d.active_run ? el("a", { class: "hero-link", href: `#/run/${d.active_run}` }, "watch live →") : null),
    el("div", { class: "hero-sub" }, d.enabled
      ? (d.next_fire ? el("span", {}, "next ", when(d.next_fire, { mode: "rel" }))
                     : (d.schedule_desc || "manual only"))
      : "scheduler off")));

  const lr = d.last_run;
  tiles.push(tile("last run",
    lr ? el("a", { class: "hero-strong hero-link row", style: "gap:6px;align-items:center",
                   href: `#/run/${lr.run_id}` }, chip(lr.state, lr.state), when(lr.ts, { mode: "rel" }))
       : el("div", { class: "hero-strong muted" }, "never run"),
    lr ? el("div", { class: "hero-sub" },
          [lr.turns != null ? `${lr.turns} turns` : null,
           lr.elapsed_s != null ? fmtDur(lr.elapsed_s) : null,
           lr.usage ? `${fmtNum((lr.usage.in || 0) + (lr.usage.out || 0))} tok` : null,
           lr.usage && lr.usage.cost ? fmtUsd(lr.usage.cost) : null].filter(Boolean).join(" · "))
       : null));

  if ((d.recent_runs || []).length)
    tiles.push(tile("recent runs", el("div", { class: "hero-hb" }, heartbeat(d.recent_runs))));

  if (d.spend && d.spend.current) {
    const c = d.spend.current;
    tiles.push(tile(`spend · ${d.spend.month || "month"}`,
      el("div", { class: "hero-strong" }, fmtUsd(c.cost || 0)),
      el("div", { class: "hero-sub" }, `${fmtNum(c.tokens || 0)} tok · ${c.runs || 0} runs`)));
  }

  const openQ = d.open_questions || (d.questions || []).filter((q) => !q.answered).length;
  if (openQ)
    tiles.push(tile("decisions",
      el("a", { class: "hero-strong hero-link", href: `#/questions?routine=${encodeURIComponent(slug)}` },
        chip(`${openQ} waiting`, "waiting_user")),
      el("div", { class: "hero-sub" }, "needs your answer")));

  return el("div", { class: "routine-hero" }, ...tiles);
}

// Slice a flat host (a sequence of <h2> followed by their panels) into sections keyed by
// heading text, then emit one <details class="rgroup"> per group. Sections not named by any
// group are kept in a trailing "More" group — nothing is dropped.
export function groupSections(host, groups) {
  const sections = new Map();
  let key = null, nodes = [];
  const flush = () => { if (key != null) sections.set(key, nodes); };
  for (const node of Array.from(host.children)) {
    if (node.tagName === "H2") { flush(); key = node.textContent.trim(); nodes = [node]; }
    else if (key != null) nodes.push(node);
  }
  flush();

  const used = new Set();
  const out = el("div", { class: "rgroups" });
  const groupEl = (title, hint, picked, open) => el("details",
    { class: "rgroup", open: open ? true : null },
    el("summary", { class: "rgroup-head" },
      el("span", { class: "rgroup-title" }, title),
      hint ? el("span", { class: "rgroup-hint" }, hint) : null),
    el("div", { class: "rgroup-body" }, ...picked));

  for (const g of groups) {
    const picked = [];
    for (const h of g.headings) {
      const k = [...sections.keys()].find((s) => s === h);
      if (k && !used.has(k)) { picked.push(...sections.get(k)); used.add(k); }
    }
    if (picked.length) out.append(groupEl(g.title, g.hint, picked, g.open !== false));
  }
  const leftover = [...sections.entries()].filter(([k]) => !used.has(k));
  if (leftover.length)
    out.append(groupEl("More", "", leftover.flatMap(([, ns]) => ns), true));
  return out;
}
