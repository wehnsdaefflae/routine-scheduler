// Dashboard: routine bays with status lamp, next fire, last outcome + its cost/turns/
// tokens/duration, open questions, run-now. A running routine pulses; one blocked on a
// question is visually loud. Meta routines are tucked away by default; tags, states and
// free text filter; every stat sorts; a table view sits one toggle away.

import { api } from "/static/api.js";
import { activityFeed } from "/static/components/activityfeed.js";
import { slugColor } from "/static/components/charts.js";
import { groupControls, groupProgress, groupsToolbar, openGroupEditor } from "/static/components/groupmanage.js";
import { heartbeat } from "/static/components/heartbeat.js";
import { cronToFriendly, specAtInstant } from "/static/components/schedule.js";
import { weekGrid } from "/static/components/weekgrid.js";
import { mdInline } from "/static/md.js";
import { chip, el, emptyState, fmtCost, fmtDur, fmtNum, skeleton, storage, tagChip, toast, when } from "/static/util.js";
import { WORKING as RUNNING } from "/static/states.js";

const VIEW_KEY = "rsched_dash_view";
const SORT_KEY = "rsched_dash_sort";
const DIR_KEY = "rsched_dash_dir";
const WEEK_KEY = "rsched_dash_week";
const ACTIVITY_KEY = "rsched_dash_activity";
const GROUPS_OPEN_KEY = "rsched_dash_groups_open";

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
  const pauseBtn = el("button", { class: "btn small", hidden: true,
    title: "skip scheduled/triggered/one-shot fires — “run now” stays available",
    onclick: async () => {
      pauseBtn.disabled = true;
      try { await api("/api/settings/pause", { method: "POST" }); toast("scheduling paused"); await load(); }
      catch (err) { toast(err.message, 4000, { error: true }); }
      pauseBtn.disabled = false;
    } }, "⏸ pause scheduling");
  view.append(
    el("div", { class: "page-head" },
      el("div", {},
        el("h1", {}, "Routines")),
      pauseBtn));
  const banner = el("div", {});
  // Week-strip drag ops (weekgrid-drag.js): every drop PATCHes config, then reloads so the
  // strip redraws from truth. Group-membership PATCHes always carry the FULL member records —
  // Reschedules ride the same schedule.friendly PATCH
  // the editors use; a custom cron has no draggable shape and is refused with a pointer to
  // its editor. `cards`/`serverTz` bind lazily — drops only happen after load() filled them.
  const fmtFireAt = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const nameOf = (slug) => cards.find((c) => c.slug === slug)?.name || slug;
  const memberRecords = (_g, order) => order.map((s) => ({ slug: s }));
  async function dropOp(fn, okMsg) {
    try { await fn(); toast(okMsg); } catch (err) { toast(err.message, 4000, { error: true }); }
    await load();
  }
  const dragHandlers = {
    reorder: (g, slug, target, after) => {
      const order = g.members.filter((s) => s !== slug);
      order.splice(order.indexOf(target) + (after ? 1 : 0), 0, slug);
      return dropOp(() => api(`/api/groups/${g.id}`, { method: "PATCH",
        body: { members: memberRecords(g, order) } }),
        `${nameOf(slug)} → position ${order.indexOf(slug) + 1} in ${g.name}`);
    },
    join: (g, slug, from) => dropOp(async () => {
      if (from) await api(`/api/groups/${from.id}`, { method: "PATCH",
        body: { members: memberRecords(from, from.members.filter((s) => s !== slug)) } });
      await api(`/api/groups/${g.id}`, { method: "PATCH",
        body: { members: [...memberRecords(g, g.members), { slug }] } });
    }, `${nameOf(slug)} joined ${g.name}`),
    leave: (g, slug) => dropOp(() => api(`/api/groups/${g.id}`, { method: "PATCH",
      body: { members: memberRecords(g, g.members.filter((s) => s !== slug)) } }),
      `${nameOf(slug)} left ${g.name}`),
    reschedule: (slug, when) => {
      const spec = specAtInstant(cronToFriendly(cards.find((c) => c.slug === slug)?.cron), when, serverTz);
      if (!spec) { toast("custom schedule — edit it on the routine page", 4000, { error: true }); return; }
      return dropOp(() => api(`/api/routines/${slug}`, { method: "PATCH",
        body: { schedule: { friendly: spec } } }), `${nameOf(slug)} → ${fmtFireAt.format(when)}`);
    },
    rescheduleGroup: (g, when) => {
      const spec = specAtInstant(cronToFriendly(g.cron), when, serverTz);
      if (!spec) { toast("custom group schedule — edit it in the group editor", 4000, { error: true }); return; }
      return dropOp(() => api(`/api/groups/${g.id}`, { method: "PATCH",
        body: { schedule: { friendly: spec } } }), `group ${g.name} → ${fmtFireAt.format(when)}`);
    },
  };
  const week = weekGrid(dragHandlers);
  const weekPanel = el("details", { class: "panel weekpanel",
    ...(storage.get(WEEK_KEY) !== "closed" ? { open: true } : {}) },
    el("summary", {}, "this week"), week.node);
  weekPanel.addEventListener("toggle", () => storage.set(WEEK_KEY, weekPanel.open ? "open" : "closed"));
  const filterBar = el("div", { class: "filterbar" });
  // D80: group management lives HERE (the /groups subpage is retired) — this bar carries
  // "+ new group" + the instance default; per-group controls sit on the group rows below.
  // Rebuilt only when the groups payload changes (its select must survive live refreshes,
  // the F229 rule), and the editors are overlays for the same reason.
  const groupsBar = el("div", { class: "panel mt", style: "padding:8px 12px" });
  const body = el("div", { class: "mt" });
  // The cross-routine activity feed (the former Log page): every run, filterable, with the
  // transcript tailing inline. Collapsed by default and lazily started — a closed section
  // neither fetches nor polls.
  const feed = activityFeed();
  const activityPanel = el("details", { class: "panel weekpanel mt activity-panel",
    ...(storage.get(ACTIVITY_KEY) === "open" ? { open: true } : {}) },
    el("summary", {}, "activity — every run across every routine"), feed.node);
  activityPanel.addEventListener("toggle", () => {
    storage.set(ACTIVITY_KEY, activityPanel.open ? "open" : "closed");
    if (activityPanel.open) feed.start();
  });
  if (activityPanel.open) feed.start();
  view.append(banner, weekPanel, filterBar, groupsBar, body, activityPanel);
  body.append(skeleton(), skeleton(), skeleton());

  let cards = [], llmReady = true, firesBySlug = new Map(), oneShotsBySlug = new Map();
  let serverTz = "";   // the zone crons are stored in — drag-reschedules re-time specs in it
  let groupData = null;   // the raw /api/groups payload — the group-management surface's input
  let groupsBySlug = new Map();   // slug -> [group records] (R107/F269 — group badges on the list)
  let groupsOrdered = [];   // [{id, name, members(slugs), …}] in fire order (F271)
  let groupSchedBySlug = new Map();   // slug -> its scheduled group (cron suppressed, R313)
  let lastTagSig = null;   // F229: only rebuild the filter bar when the tag set changes
  let lastGroupSig = null; // same rule for the groups bar: its select must survive refreshes
  const states = new Set();
  // D72: the table IS the default (operator, 2026-08-05) — denser, sortable, and where the
  // group rows live. The card grid stays one toggle away and a user's choice persists.
  let viewMode = storage.get(VIEW_KEY) || "list";
  let sortKey = storage.get(SORT_KEY) || "activity";
  // F208: an explicit sort DIRECTION, so re-clicking the active column reverses it instead
  // of being a no-op. "" = the column's natural direction (from SORTS); "asc"/"desc" override.
  let sortDir = storage.get(DIR_KEY) || "";
  let search = "";

  function visible(c) {
    if (states.size && ![...states].some((s) => STATE_BUCKETS[s]?.(c))) return false;
    if (search) {
      const hay = `${c.name} ${c.slug} ${c.description} ${(c.tags || []).join(" ")}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  }

  function ordered(list) {
    const [, valueOf, desc] = SORTS[sortKey] || SORTS.activity;
    // sortDir (F208) overrides the column's natural direction when the user toggled it.
    const descending = sortDir ? sortDir === "desc" : desc;
    return [...list].sort((a, b) => {
      const va = valueOf(a), vb = valueOf(b);
      const cmp = typeof va === "string" ? va.localeCompare(vb) : va - vb;
      return descending ? -cmp : cmp;
    });
  }

  function renderFilterBar() {
    filterBar.replaceChildren();
    if (!cards.length) return;
    // Tag chips retired (user order 2026-08-12: they ate a whole row; the search field
    // still matches tags — visible()'s haystack includes them).
    filterBar.append(el("span", { class: "lbl" }, "state"));
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
      // renderFilterBar too: the toggle's own label must flip immediately (it used to stay
      // stale until a tag change happened to rebuild the bar). A deliberate click may tear
      // down the search input — the F229 focus concern only guards LIVE refreshes.
      onclick: () => { viewMode = viewMode === "cards" ? "list" : "cards"; storage.set(VIEW_KEY, viewMode); renderFilterBar(); renderBody(); } },
      viewMode === "cards" ? "☰ list view" : "▦ card view");
    filterBar.append(sortSel, searchIn, toggle);
    if (states.size) filterBar.append(el("button", { class: "btn ghost small",
      onclick: () => { states.clear(); renderFilterBar(); renderBody(); },
    }, "clear"));
  }

  function renderBody() {
    const shown = ordered(cards.filter(visible));
    week.update(cards.filter(visible), firesBySlug, oneShotsBySlug, groupsOrdered);
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
      [routines, status, sched, groupData] = await Promise.all([
        api("/api/routines"), api("/api/status").catch(() => ({})),
        api("/api/schedule/week").catch(() => null),
        // Group membership is a nicety on this page — a groups-fetch hiccup must never blank
        // the routines list, so it degrades to "no groups" rather than throwing (R107, F269).
        api("/api/groups").catch(() => null),
      ]);
    } catch (err) {
      body.replaceChildren(emptyState("✕", "Couldn't reach the daemon", err.message));
      return;
    }
    cards = routines;
    serverTz = groupData?.server_tz || "";
    // slug -> [group records]: each routine card/row shows which group(s) it belongs to —
    // the chips open the group editor (D80: this page IS the group-management surface).
    groupsBySlug = new Map();
    // members are RECORDS {slug} in the store; the display list keeps plain
    // slugs (what the week grid + rows consume).
    // `fires` are the GROUP's cron fire times from the week payload (D71) — a scheduled
    // group's members carry no fires of their own, the chain is drawn from these.
    const groupFires = new Map((sched?.groups || [])
      .map((g) => [g.id, g.fires.map((t) => +new Date(t))]));
    groupsOrdered = (groupData?.groups || [])
      .map((g) => ({ id: g.id, name: g.name,
                     members: (g.members || []).map((m) => m.slug),
                     schedule_desc: g.schedule_desc || "", cron: g.cron || "",
                     paused: !!g.paused, fires: groupFires.get(g.id) || [] }));
    // slug -> its SCHEDULED group (the first, matching the server's group_managed rule):
    // that group's cron suppresses the member's own, so the member's real schedule is the
    // group's — rendering the vestigial member cron would be a lie (R313)
    groupSchedBySlug = new Map();
    for (const g of groupsOrdered) {
      for (const slug of g.members) {
        if (g.cron && !groupSchedBySlug.has(slug)) groupSchedBySlug.set(slug, g);
      }
    }
    for (const g of groupData?.groups || []) {
      for (const m of g.members || []) {
        if (!groupsBySlug.has(m.slug)) groupsBySlug.set(m.slug, []);
        groupsBySlug.get(m.slug).push(g);
      }
    }
    firesBySlug = new Map((sched?.routines || []).map((r) => [r.slug, r.fires.map((t) => +new Date(t))]));
    oneShotsBySlug = new Map((sched?.routines || []).map((r) => [r.slug, (r.one_shots || []).map((t) => +new Date(t))]));
    llmReady = status.llm_ready !== false;
    banner.replaceChildren();
    if (!llmReady) banner.append(el("div", { class: "panel warn", style: "margin:12px 0" },
      el("strong", {}, "No model connected — "),
      el("span", { class: "muted" }, "add an endpoint and set the system model in "),
      el("a", { href: "#/settings" }, "Settings"),
      el("span", { class: "muted" }, " to create or run routines.")));
    pauseBtn.hidden = !!status.paused;   // while paused the banner owns the control
    if (status.paused) banner.append(el("div", { class: "panel warn", style: "margin:12px 0" },
      el("strong", {}, "⏸ Scheduling is paused — "),
      el("span", { class: "muted" },
        "no scheduled, triggered or one-shot runs fire; “▶ run now” still works. "),
      el("button", { class: "btn small primary", onclick: async (e) => {
        e.target.disabled = true;
        try { await api("/api/settings/pause", { method: "DELETE" }); toast("scheduling resumed"); await load(); }
        catch (err) { toast(err.message, 4000, { error: true }); e.target.disabled = false; }
      } }, "▶ resume scheduling")));
    // F229: build the filter bar ONCE. It holds the search <input> and sort <select>;
    // replaceChildren() on every live bus refresh (~every 600ms while ≥1 routine runs)
    // destroyed a user's focus and half-typed search text. Tag chips retired 2026-08-12
    // (user order) — the bar's content no longer varies with data, so once is enough;
    // state-chip toggles rebuild it themselves.
    if (lastTagSig === null) {
      lastTagSig = "built";
      renderFilterBar();
    }
    // The groups bar follows the same only-on-change rule (its select must survive live
    // refreshes); in_flight is deliberately OUT of the signature — chain progress renders
    // on the group rows, not here.
    const groupSig = groupData
      ? JSON.stringify([groupData.default_on_failure, groupData.known_routines])
      : null;
    if (groupSig !== lastGroupSig) {
      lastGroupSig = groupSig;
      groupsBar.replaceChildren();
      groupsBar.hidden = !groupData;
      if (groupData) groupsBar.append(groupsToolbar(groupData, { reload: load }));
    }
    renderBody();
  }

  // Table rows run ICON-ONLY controls (horizontal space, D72 follow-up); cards keep the
  // labelled versions. The resume glyph is the HOLLOW ▷ so it can never be mistaken for
  // the filled ▶ run-now sitting beside it — the action text lives in the hover title.
  function runNowBtn(c, cls = "btn small primary", icon = false) {
    return el("button", {
      class: cls,
      disabled: !llmReady,
      title: llmReady ? (icon ? "run now" : "") : "connect an LLM endpoint in Settings first",
      onclick: async (e) => {
        e.target.disabled = true;
        try {
          const r = await api(`/api/routines/${c.slug}/run`, { method: "POST" });
          location.hash = `#/run/${r.run_id}`;
        } catch (err) { toast(err.message, 4000, { error: true }); e.target.disabled = false; }
      },
    }, icon ? "▶" : "▶ run now");
  }

  // D72: start/pause without the config page — one PATCH on `enabled`, from both views.
  // While a run is active the web layer refuses config edits (409 guard_not_active), so the
  // control disables itself instead of letting the click bounce into an error toast.
  function enableToggle(c, cls = "btn small ghost", icon = false) {
    const on = !!c.enabled;
    return el("button", {
      class: cls,
      disabled: !!c.active_run,
      title: c.active_run ? "a run is active — pausing waits until it ends"
        : on ? "pause this routine — schedule, triggers and one-shots stop firing (“▶ run now” still works)"
        : "resume this routine's schedule",
      onclick: async (e) => {
        e.target.disabled = true;
        try {
          await api(`/api/routines/${c.slug}`, { method: "PATCH", body: { enabled: !on } });
          toast(on ? `${c.name || c.slug} paused — nothing fires until resumed`
                   : `${c.name || c.slug} resumed`);
          await load();
        } catch (err) { toast(err.message, 4000, { error: true }); e.target.disabled = false; }
      },
    }, on ? (icon ? "⏸" : "⏸ pause") : (icon ? "▷" : "▷ resume"));
  }

  // The routine's identity color (charts.slugColor — the same hash the week strip's bars
  // use): with the strip's legend gone, the swatch on the row/card IS the color mapping.
  function swatch(slug) {
    return el("span", { class: "id-swatch", style: `background:${slugColor(slug)}`,
      title: "this routine's color in the week strip" });
  }

  // The schedule a routine will ACTUALLY fire on. A member of a scheduled group has its
  // own cron suppressed by the daemon — showing that vestigial cron here read as a lie
  // (R313), so group-managed rows show the group's schedule instead.
  function schedText(c) {
    const g = groupSchedBySlug.get(c.slug);
    if (g) return `⛓ ${g.name} — ${g.paused ? "group paused" : (g.schedule_desc || "scheduled")}`;
    return null;   // not group-managed → the caller renders the routine's own desc
  }

  // Group membership as a chip row — each chip opens the group's editor right here (D80:
  // this page is the group-management surface). null when in no group.
  function groupChips(slug) {
    const gs = groupsBySlug.get(slug) || [];
    if (!gs.length) return null;
    return el("div", { class: "groups-row" },
      el("span", { class: "lbl small" }, "⛓"),
      ...gs.map((g) => el("button", { class: "chip group-chip",
        title: `in group “${g.name}” — edit the group`,
        onclick: (e) => { e.stopPropagation(); openGroupEditor(g, groupData, { reload: load }); },
      }, g.name)));
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
        swatch(c.slug),
        el("a", { href: `#/routine/${c.slug}` }, c.name || c.slug),
        stateChip),
      (c.tags || []).length ? el("div", { class: "tags" }, c.tags.map((t) => tagChip(t))) : null,
      groupChips(c.slug),
      c.description ? el("div", { class: "desc" }, c.description) : null,
      blocked ? el("div", { class: "qflag" },
        el("span", {}, "waiting on your answer"),
        el("a", { class: "btn small primary", href: "#/questions", style: "margin-left:auto" }, "decide")) : null,
      el("div", { class: "meta" },
        el("span", schedText(c) ? { title: "group-managed — the group's schedule fires this routine; its own cron is suppressed" } : {},
          `⏱ ${schedText(c) || c.schedule_desc || "Manual"}`),
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
        enableToggle(c),
        last ? el("a", { class: "btn small", href: `#/run/${last.run_id}` }, "last run") : null));
  }

  // ---- the detail table: same data, one row per routine, headers sort ------------------------
  // Compressed to five columns (operator ask — the twelve-column layout outgrew the screen):
  // state folds into the history strip (newest bar = last outcome, hover for detail; live/
  // attention row styling and the dimmed disabled row carry the rest), schedule+next stack in
  // one cell, the last run stacks its ts over the turns·duration·tokens·cost line, and open
  // questions ride the routine cell as a chip. Every dropped header's sort key stays
  // reachable in the filter bar's sort select.
  const COLS = [
    ["routine", "name"], ["history", null], ["schedule · next", "next"],
    ["last run", "activity"], ["", null],
  ];
  function table(shown) {
    const head = el("tr", {}, COLS.map(([label, key]) => el("th",
      key ? { style: "cursor:pointer", title: "sort by this column (click again to reverse)",
              onclick: () => {
                if (key === sortKey) {
                  // re-click the active column → flip direction (F208)
                  const cur = sortDir || (SORTS[key]?.[2] ? "desc" : "asc");
                  sortDir = cur === "desc" ? "asc" : "desc";
                } else {
                  sortKey = key; sortDir = "";     // new column → its natural direction
                }
                storage.set(SORT_KEY, sortKey); storage.set(DIR_KEY, sortDir);
                renderFilterBar(); renderBody();
              } }
          : {},
      label + (key === sortKey
        ? ((sortDir || (SORTS[key]?.[2] ? "desc" : "asc")) === "desc" ? " ▾" : " ▴")
        : ""))));
    // U-order (user, 2026-08-13): inside an expanded group the row order IS the fire
    // order, so the rows themselves are the reorder surface — drag one onto a sibling
    // (upper half = before it, lower half = after). The editor's ↑/↓ stays for precision.
    let dragFrom = null;
    const rowFor = (c, extraCls = "", group = null) => {
      const last = c.last_run;
      const rowCls = [RUNNING.has(c.active_state) ? "live" : "",
        c.active_state === "waiting_user" ? "attention" : "",
        c.enabled ? "" : "disabled-row", extraCls]
        .filter(Boolean).join(" ");
      const stats = statsLine(last);
      const tr = el("tr", { class: rowCls },
        el("td", {}, swatch(c.slug), el("a", { href: `#/routine/${c.slug}` }, c.name || c.slug),
          c.open_questions ? el("a", { href: "#/questions", class: "chip blocking",
            title: "open questions waiting for you" }, `${c.open_questions} open ?`) : null,
          c.description ? el("div", { class: "faint small", style: "max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, c.description) : null,
          groupChips(c.slug)),
        el("td", { class: "hb-cell" }, c.recent_runs?.length
          ? heartbeat(c.recent_runs) : el("span", { class: "faint" }, "—")),
        el("td", { class: "muted small" },
          el("div", schedText(c)
            ? { title: "group-managed — the group's schedule fires this routine; its own cron is suppressed" }
            : {},
            // a disabled routine's always-visible marker: the row dim alone was too subtle
            c.enabled ? null : el("span", { class: "chip disabled", style: "margin-right:6px",
              title: "paused — nothing fires until resumed" }, "off"),
            schedText(c) || c.schedule_desc || "manual"),
          c.next_fire ? el("div", { class: "faint" }, "next ", when(c.next_fire, { mode: "rel" })) : null),
        el("td", {}, last
          ? [el("a", { href: `#/run/${last.run_id}` }, when(last.ts)),
             stats ? el("div", { class: "faint small",
               title: "last run: turns · duration · tokens · cost" }, stats) : null]
          : el("span", { class: "faint" }, "never")),
        el("td", { class: "row-actions" },
          c.active_run
            ? el("a", { class: "btn small", href: `#/run/${c.active_run}`, title: "watch the live run" }, "◉")
            : runNowBtn(c, "btn small", true),
          enableToggle(c, "btn small ghost", true)));
      if (group) {
        tr.draggable = true;
        tr.dataset.dragMember = c.slug;
        tr.ondragstart = (e) => {
          dragFrom = { gid: group.id, slug: c.slug };
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", c.slug);
        };
        tr.ondragover = (e) => {
          if (!dragFrom || dragFrom.gid !== group.id || dragFrom.slug === c.slug) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          tr.classList.add("drop-here");
        };
        tr.ondragleave = () => tr.classList.remove("drop-here");
        tr.ondragend = () => { dragFrom = null; };
        tr.ondrop = async (e) => {
          e.preventDefault();
          tr.classList.remove("drop-here");
          const from = dragFrom; dragFrom = null;
          if (!from || from.gid !== group.id || from.slug === c.slug) return;
          const raw = (groupData?.groups || []).find((r) => r.id === group.id);
          const rec = raw?.members?.find((m) => m.slug === from.slug);
          if (!rec) return;
          const list = raw.members.filter((m) => m.slug !== from.slug);
          const at = list.findIndex((m) => m.slug === c.slug);
          if (at < 0) return;
          const box = tr.getBoundingClientRect();
          const before = e.clientY < box.top + box.height / 2;
          list.splice(at + (before ? 0 : 1), 0, rec);
          try {
            await api(`/api/groups/${group.id}`, { method: "PATCH", body: { members: list } });
            toast(`“${group.name}” fire order: ${list.map((m) => m.slug).join(" → ")}`);
          } catch (ex) { toast(ex.message, 4000, { error: true }); }
          load();
        };
      }
      return tr;
    };
    // D73 + F281: each group is its own collapsible row — expanding lists its members
    // right beneath it, in the group's FIRE order (not the table sort). A grouped routine
    // lives ONLY under its group row (reviewer order 2026-08-06: the flat list used to
    // repeat every grouped routine, so the table double-listed them); the flat sorted
    // list below carries just the ungrouped rest. Expansion persists like the view mode.
    const openGroups = new Set(JSON.parse(storage.get(GROUPS_OPEN_KEY) || "[]"));
    const bySlug = new Map(shown.map((c) => [c.slug, c]));
    const rows = [];
    for (const g of groupsOrdered) {
      const members = g.members.map((s) => bySlug.get(s)).filter(Boolean);
      if (!members.length) continue;                 // fully filtered out → no header either
      const open = openGroups.has(g.name);
      const raw = (groupData?.groups || []).find((r) => r.id === g.id);
      // D80: the group row carries its management — run now / pause / edit (the buttons
      // stopPropagation so the row's expand toggle keeps working), plus the in-flight
      // chain's per-pass progress (F292).
      const controls = raw ? groupControls(raw, groupData, { reload: load }) : [];
      const progress = raw ? groupProgress(raw, groupData) : null;
      rows.push(el("tr", { class: "group-row", "data-group-row": g.id },
        el("td", { colSpan: COLS.length,
          title: open ? "collapse this group's rows" : "expand this group's member rows",
          onclick: () => {
            open ? openGroups.delete(g.name) : openGroups.add(g.name);
            storage.set(GROUPS_OPEN_KEY, JSON.stringify([...openGroups]));
            renderBody();
          } },
          el("div", { class: "row", style: "justify-content:space-between;align-items:center;gap:8px" },
            el("span", {},
              el("span", { class: "tri" }, open ? "▾ " : "▸ "),
              `⛓ ${g.name}`,
              g.paused ? el("span", { class: "muted small", "data-group-paused": "",
                style: "margin-left:8px" }, "⏸ paused") : null,
              el("span", { class: "faint small", style: "margin-left:8px" },
                `${members.length} routine${members.length === 1 ? "" : "s"} · fire order`
                + (g.cron ? ` · ${g.paused ? "paused" : g.schedule_desc}` : "")),
              progress ? el("span", { style: "margin-left:8px" }, progress) : null),
            el("span", { class: "row", style: "gap:6px" }, ...controls)))));
      if (open) for (const m of members) rows.push(rowFor(m, "group-member", g));
    }
    const grouped = new Set(groupsOrdered.flatMap((g) => g.members));
    for (const c of shown) if (!grouped.has(c.slug)) rows.push(rowFor(c));
    // Five compressed columns fit the normal shell column — the D72 full-width breakout
    // existed for the old twelve-column layout and is retired with it.
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
  return () => {
    window.removeEventListener("rsched-bus", onBus);
    clearTimeout(pending);
    feed.dispose();
  };
}
