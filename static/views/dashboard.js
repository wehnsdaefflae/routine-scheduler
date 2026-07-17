// Dashboard: routine bays with status lamp, next fire, last outcome + its cost/turns/
// tokens/duration, open questions, run-now. A running routine pulses; one blocked on a
// question is visually loud. Meta routines are tucked away by default; tags, states and
// free text filter; every stat sorts; a table view sits one toggle away.

import { api } from "/static/api.js";
import { heartbeat } from "/static/components/heartbeat.js";
import { weekGrid } from "/static/components/weekgrid.js";
import { mdInline } from "/static/md.js";
import { chip, el, emptyState, fmtCost, fmtDur, fmtNum, skeleton, storage, tagChip, toast, when } from "/static/util.js";
import { WORKING as RUNNING } from "/static/states.js";

const FILTER_KEY = "rsched_dash_tags";
const VIEW_KEY = "rsched_dash_view";
const SORT_KEY = "rsched_dash_sort";
const WEEK_KEY = "rsched_dash_week";

// ---- sort keys: [label, value-fn, descending?] -------------------------------------------------
const tokensOf = (c) => (c.last_run?.usage?.in || 0) + (c.last_run?.usage?.out || 0);
const SORTS = {
  activity: ["recent activity", (c) => c.last_run?.ts || "", true],
  name: ["name", (c) => (c.name || c.slug).toLowerCase(), false],
  next: ["next run", (c) => c.next_fire || "9999", false],
  state: ["state", (c) => c.active_state || (c.last_run?.state ?? "zz"), false],
  cost: ["last cost", (c) => c.last_run?.usage?.cost || 0, true],
  tokens: ["last tokens", tokensOf, true],
  turns: ["last turns", (c) => c.last_run?.turns || 0, true],
  duration: ["last duration", (c) => c.last_run?.elapsed_s || 0, true],
  questions: ["open questions", (c) => c.open_questions || 0, true],
};
// coarse run-state buckets for the state filter chips
const STATE_BUCKETS = {
  active: (c) => RUNNING.has(c.active_state),
  waiting: (c) => c.active_state === "waiting_user" || (c.open_questions || 0) > 0,
  ok: (c) => !c.active_state && c.last_run?.state === "finished",
  failed: (c) => !c.active_state && ["failed", "aborted"].includes(c.last_run?.state),
  disabled: (c) => !c.enabled,
};

// "Jul: 1.2M tok · $4.31 (Jun: 0.9M · $3.10)" — the durable monthly series, not last-run
function spendLine(spend) {
  if (!spend?.current) return null;
  const cell = (c) => c ? [fmtNum(c.tokens) + " tok", fmtCost({ cost: c.cost })].filter(Boolean).join(" · ") : "—";
  const monthName = (m) => new Date(m + "-01T00:00:00").toLocaleString("en", { month: "short" });
  let text = `${monthName(spend.month)}: ${cell(spend.current)}`;
  if (spend.prev) text += `  (${monthName(spend.prev_month)}: ${cell(spend.prev)})`;
  const growing = spend.prev && spend.current.tokens > spend.prev.tokens * 1.2;
  return el("div", { class: "muted small",
    title: "this month's spend from the durable usage stream (survives run retention)" },
    text, growing ? el("span", { class: "chip partial", style: "margin-left:6px" }, "↑ growing") : null);
}

function statsLine(run) {
  if (!run) return "";
  const parts = [];
  if (run.turns) parts.push(`${run.turns} turns`);
  if (run.elapsed_s != null) parts.push(fmtDur(run.elapsed_s));
  const tok = (run.usage?.in || 0) + (run.usage?.out || 0);
  if (tok) parts.push(`${fmtNum(tok)} tok`);
  const cost = fmtCost(run.usage);
  if (cost) parts.push(cost);
  return parts.join(" · ");
}

export async function render(view) {
  view.append(
    el("div", { class: "page-head" },
      el("div", {},
        el("div", { class: "kicker" }, "console / routines"),
        el("h1", {}, "Routines"))));
  const banner = el("div", {});
  const week = weekGrid();
  const weekPanel = el("details", { class: "panel weekpanel",
    ...(storage.get(WEEK_KEY) !== "closed" ? { open: true } : {}) },
    el("summary", {}, "this week"), week.node);
  weekPanel.addEventListener("toggle", () => storage.set(WEEK_KEY, weekPanel.open ? "open" : "closed"));
  const filterBar = el("div", { class: "filterbar" });
  const body = el("div", { class: "mt" });
  view.append(banner, weekPanel, filterBar, body);
  body.append(skeleton(), skeleton(), skeleton());

  let cards = [], llmReady = true, firesBySlug = new Map();
  const active = new Set(JSON.parse(storage.get(FILTER_KEY) || "[]"));
  const states = new Set();
  let viewMode = storage.get(VIEW_KEY) || "cards";
  let sortKey = storage.get(SORT_KEY) || "activity";
  let search = "";

  function visible(c) {
    const tags = c.tags || [];
    if (active.size && !tags.some((t) => active.has(t))) return false;
    if (states.size && ![...states].some((s) => STATE_BUCKETS[s]?.(c))) return false;
    if (search) {
      const hay = `${c.name} ${c.slug} ${c.description} ${(c.tags || []).join(" ")}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  }

  function ordered(list) {
    const [, valueOf, desc] = SORTS[sortKey] || SORTS.activity;
    return [...list].sort((a, b) => {
      const va = valueOf(a), vb = valueOf(b);
      const cmp = typeof va === "string" ? va.localeCompare(vb) : va - vb;
      return desc ? -cmp : cmp;
    });
  }

  function renderFilterBar() {
    const all = [...new Set(cards.flatMap((c) => c.tags || []))]
      .sort((a, b) => a.localeCompare(b));
    filterBar.replaceChildren();
    if (!cards.length) return;
    filterBar.append(el("span", { class: "lbl" }, "filter"));
    for (const t of all) {
      filterBar.append(tagChip(t, {
        active: active.has(t),
        onClick: () => {
          active.has(t) ? active.delete(t) : active.add(t);
          storage.set(FILTER_KEY, JSON.stringify([...active]));
          renderFilterBar(); renderBody();
        },
      }));
    }
    filterBar.append(el("span", { class: "lbl", style: "margin-left:10px" }, "state"));
    for (const s of Object.keys(STATE_BUCKETS)) {
      filterBar.append(tagChip(s, {
        active: states.has(s),
        onClick: () => { states.has(s) ? states.delete(s) : states.add(s); renderFilterBar(); renderBody(); },
      }));
    }
    const sortSel = el("select", { style: "margin-left:10px" },
      Object.entries(SORTS).map(([k, [label]]) => el("option", { value: k }, `sort: ${label}`)));
    sortSel.value = sortKey;
    sortSel.onchange = () => { sortKey = sortSel.value; storage.set(SORT_KEY, sortKey); renderBody(); };
    const searchIn = el("input", { type: "search", placeholder: "search…", value: search,
      style: "width:130px;margin-left:6px" });
    searchIn.oninput = () => { search = searchIn.value.trim().toLowerCase(); renderBody(); };
    const toggle = el("button", { class: "btn ghost small", style: "margin-left:6px",
      title: "switch between the card grid and a sortable detail table",
      onclick: () => { viewMode = viewMode === "cards" ? "list" : "cards"; storage.set(VIEW_KEY, viewMode); renderBody(); } },
      viewMode === "cards" ? "☰ list view" : "▦ card view");
    filterBar.append(sortSel, searchIn, toggle);
    if (active.size || states.size) filterBar.append(el("button", { class: "btn ghost small",
      onclick: () => { active.clear(); states.clear(); storage.set(FILTER_KEY, "[]"); renderFilterBar(); renderBody(); },
    }, "clear"));
  }

  function renderBody() {
    const shown = ordered(cards.filter(visible));
    week.update(cards.filter(visible), firesBySlug);
    weekPanel.hidden = !cards.length;
    body.replaceChildren();
    if (!cards.length) {
      body.append(emptyState("◌", "No routines yet",
        "Create the first one with “+ new routine” — describe the task, answer a few questions, and it schedules itself."));
      return;
    }
    if (!shown.length) {
      body.append(emptyState("▢", "Nothing matches this filter",
        "Clear the filters above to see all routines."));
      return;
    }
    if (viewMode === "list") { body.append(table(shown)); return; }
    const grid = el("div", { class: "grid" });
    for (const c of shown) grid.append(card(c));
    body.append(grid);
  }

  async function load() {
    let routines, status, sched;
    try {
      [routines, status, sched] = await Promise.all([
        api("/api/routines"), api("/api/status").catch(() => ({})),
        api("/api/schedule/week").catch(() => null),
      ]);
    } catch (err) {
      body.replaceChildren(emptyState("✕", "Couldn't reach the daemon", err.message));
      return;
    }
    cards = routines;
    firesBySlug = new Map((sched?.routines || []).map((r) => [r.slug, r.fires.map((t) => +new Date(t))]));
    llmReady = status.llm_ready !== false;
    banner.replaceChildren();
    if (!llmReady) banner.append(el("div", { class: "panel warn", style: "margin:12px 0" },
      el("strong", {}, "No model connected — "),
      el("span", { class: "muted" }, "add an endpoint and set the system model in "),
      el("a", { href: "#/settings" }, "Settings"),
      el("span", { class: "muted" }, " to create or run routines.")));
    renderFilterBar();
    renderBody();
  }

  function runNowBtn(c, cls = "btn small primary") {
    return el("button", {
      class: cls,
      disabled: !llmReady,
      title: llmReady ? "" : "connect an LLM endpoint in Settings first",
      onclick: async (e) => {
        e.target.disabled = true;
        try {
          const r = await api(`/api/routines/${c.slug}/run`, { method: "POST" });
          location.hash = `#/run/${r.run_id}`;
        } catch (err) { toast(err.message, 4000, { error: true }); e.target.disabled = false; }
      },
    }, "▶ run now");
  }

  function card(c) {
    const stateChip = c.active_state ? chip(c.active_state, c.active_state)
      : c.enabled ? chip("idle", "idle") : chip("disabled", "disabled");
    const last = c.last_run;
    const blocked = c.active_state === "waiting_user";
    const cls = ["card", RUNNING.has(c.active_state) ? "live" : "", blocked ? "attention" : ""]
      .filter(Boolean).join(" ");
    const stats = statsLine(last);
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
          title: "open questions waiting for you" }, `${c.open_questions} open question${c.open_questions > 1 ? "s" : ""}`) : null,
        c.decision_backlog ? el("a", { href: "#/questions", class: "chip failed",
          title: "this routine is starving on deferred decisions — answer some" }, "decision backlog") : null),
      spendLine(c.spend),
      // the past mirror of "next fire": the last runs at a glance — flaky ≠ green-today
      c.recent_runs?.length ? el("div", { class: "hb-row" }, heartbeat(c.recent_runs)) : null,
      last ? el("div", { class: "lastrun" },
          el("div", { class: "lr-line" }, when(last.ts), chip(last.state, last.state),
            stats ? el("span", { class: "muted small", title: "last run: turns · duration · tokens · cost" },
              stats) : null),
          el("div", { class: "lr-sum", title: last.summary || "" },
            mdInline((last.summary || "").split("\n").find((l) => l.trim()) || "(no summary)")))
        : el("div", { class: "lastrun" }, el("div", { class: "lr-sum faint" }, "never ran")),
      c.problems?.length ? el("div", { class: "problem" }, `⚠ ${c.problems[0]}`) : null,
      el("div", { class: "actions" },
        c.active_run
          ? el("a", { class: "btn small", href: `#/run/${c.active_run}` }, "◉ watch live")
          : runNowBtn(c),
        last ? el("a", { class: "btn small", href: `#/run/${last.run_id}` }, "last run") : null));
  }

  // ---- the detail table: same data, one row per routine, headers sort ------------------------
  const COLS = [
    ["routine", "name"], ["state", "state"], ["history", null], ["schedule", null],
    ["next", "next"], ["last run", "activity"], ["turns", "turns"], ["tokens", "tokens"],
    ["cost", "cost"], ["duration", "duration"], ["open ?", "questions"], ["", null],
  ];
  function table(shown) {
    const head = el("tr", {}, COLS.map(([label, key]) => el("th",
      key ? { style: "cursor:pointer", title: "sort by this column",
              onclick: () => { sortKey = key; storage.set(SORT_KEY, key); renderFilterBar(); renderBody(); } }
          : {},
      label + (key === sortKey ? " ▾" : ""))));
    const rows = shown.map((c) => {
      const last = c.last_run;
      const tok = tokensOf(c);
      return el("tr", { class: c.active_state === "waiting_user" ? "attention" : "" },
        el("td", {}, el("a", { href: `#/routine/${c.slug}` }, c.name || c.slug),
          c.description ? el("div", { class: "faint small", style: "max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, c.description) : null),
        el("td", {}, c.active_state ? chip(c.active_state, c.active_state)
          : c.enabled ? (last ? chip(last.state, last.state) : chip("idle", "idle")) : chip("disabled", "disabled")),
        el("td", { class: "hb-cell" }, c.recent_runs?.length
          ? heartbeat(c.recent_runs) : el("span", { class: "faint" }, "—")),
        el("td", { class: "muted small" }, c.schedule_desc || "manual"),
        el("td", { class: "muted small" }, c.next_fire ? when(c.next_fire, { mode: "rel" }) : "—"),
        el("td", {}, last ? el("a", { href: `#/run/${last.run_id}` }, when(last.ts)) : el("span", { class: "faint" }, "never")),
        el("td", { class: "num" }, last?.turns ? String(last.turns) : "—"),
        el("td", { class: "num" }, tok ? fmtNum(tok) : "—"),
        el("td", { class: "num" }, fmtCost(last?.usage) || "—"),
        el("td", { class: "num" }, last?.elapsed_s != null ? fmtDur(last.elapsed_s) : "—"),
        el("td", { class: "num" }, c.open_questions
          ? el("a", { href: "#/questions", class: "chip blocking" }, String(c.open_questions)) : "—"),
        el("td", {}, c.active_run
          ? el("a", { class: "btn small", href: `#/run/${c.active_run}` }, "◉ live")
          : runNowBtn(c, "btn small")));
    });
    return el("div", { class: "panel", style: "padding:0" },
      el("div", { class: "tablewrap" },
        el("table", { class: "list" }, el("thead", {}, head), el("tbody", {}, rows))));
  }

  await load();
  let pending = null;
  const onBus = () => {
    clearTimeout(pending);
    pending = setTimeout(() => load().catch(() => {}), 600);   // debounce bursts of bus events
  };
  window.addEventListener("rsched-bus", onBus);
  return () => { window.removeEventListener("rsched-bus", onBus); clearTimeout(pending); };
}
