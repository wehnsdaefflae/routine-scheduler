// Dashboard: routine bays with status lamp, next fire, last outcome + its cost/turns/
// tokens/duration, open questions, run-now. A running routine pulses; one blocked on a
// question is visually loud. Meta routines are tucked away by default; tags, states and
// free text filter; every stat sorts; a table view sits one toggle away.
//
// It is also the surface for the two structures a routine sits in (docs/lanes-domains.md):
// LANES are rows in the table, because a lane is a firing order over routines and belongs
// beside them; DOMAINS get their own section, because a domain is a config surface with no
// place in a schedule. A routine has at most one of each.

import { api } from "/static/api.js";
import { activityFeed } from "/static/components/activityfeed.js";
import { domainsSection } from "/static/views/dashboard-domains.js";
import { routineRows } from "/static/views/dashboard-rows.js";
import { loadQuota } from "/static/components/quota.js";
import { lanesToolbar } from "/static/components/lanemanage.js";
import { cronToFriendly, specAtInstant } from "/static/components/schedule.js";
import { weekGrid } from "/static/components/weekgrid.js";
import { el, emptyState, skeleton, storage, tagChip, toast, toastError } from "/static/util.js";
import { WORKING as RUNNING } from "/static/states.js";

const VIEW_KEY = "rsched_dash_view";
const SORT_KEY = "rsched_dash_sort";
const DIR_KEY = "rsched_dash_dir";
const WEEK_KEY = "rsched_dash_week";
const ACTIVITY_KEY = "rsched_dash_activity";
const DOMAINS_KEY = "rsched_dash_domains";

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
  // FINISHED before DISABLED, never both: a routine that reached its final goal is a different
  // thing from one you switched off — lumping them lost the only state that says "this job is
  // over". `retired` is derived from the goal document; `enabled` is your switch.
  finished: (c) => c.retired,
  disabled: (c) => !c.enabled && !c.retired,
};

export async function render(view) {
  const pauseBtn = el("button", { class: "btn small", hidden: true,
    title: "pause active routines after their current turn and stop automatic starts — “run now” stays available",
    onclick: async () => {
      pauseBtn.disabled = true;
      try { await api("/api/settings/pause", { method: "POST" }); toast("scheduling paused"); await load(); }
      catch (err) { toastError(err); }
      pauseBtn.disabled = false;
    } }, "⏸ pause scheduling");
  // The subscription's remaining quota, on the page the operator opens. It lived only on a
  // Settings endpoint card as muted 11px text, which is why the answer to "why don't I see it"
  // was partly "you would have had to go looking". SIGNAL while there is room, SUMMONS when a
  // window is nearly spent — an exhausted quota is a thing that waits on a person.
  const quotaChip = el("span", { class: "chip bare", hidden: true });
  // The chip reads "7d 57% left" and nothing on it said 57% of WHAT. The subject goes beside it
  // rather than inside it: `.chip` is white-space:nowrap, and a longer chip is what once broke
  // the horizontal viewport on a phone (where the label hides — see base.css).
  const quotaLbl = el("span", { class: "lbl quota-lbl", hidden: true }, "Claude quota");
  view.append(
    el("div", { class: "page-head" },
      el("div", {},
        el("h1", {}, "Routines")),
      el("div", { class: "row" }, quotaLbl, quotaChip, pauseBtn)));
  loadQuota(quotaChip).then(() => { quotaLbl.hidden = quotaChip.hidden; });
  const banner = el("div", {});
  // Week-strip drag ops (weekgrid-drag.js): every drop PATCHes, then reloads so the strip
  // redraws from truth. Lane-membership PATCHes always carry the FULL member record list —
  // the API replaces it wholesale — and reschedules ride the same schedule.friendly PATCH
  // the editors use; a custom cron has no draggable shape and is refused with a pointer to
  // its editor. A drop moves only TIMING: a routine's domain — and therefore what it shares —
  // is untouched by every one of these. `cards`/`serverTz` bind lazily — drops only happen
  // after load() filled them.
  const fmtFireAt = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const nameOf = (slug) => cards.find((c) => c.slug === slug)?.name || slug;
  const memberRecords = (order) => order.map((s) => ({ slug: s }));
  async function dropOp(fn, okMsg) {
    try { await fn(); toast(okMsg); } catch (err) { toastError(err); }
    await load();
  }
  // The handler NAMES are weekgrid-drag.js's contract; the lane records they take are the
  // display shape renderBody hands the strip (slug members, not the store's records).
  const dragHandlers = {
    reorder: (lane, slug, target, after) => {
      const order = lane.members.filter((s) => s !== slug);
      order.splice(order.indexOf(target) + (after ? 1 : 0), 0, slug);
      return dropOp(() => api(`/api/lanes/${lane.id}`, { method: "PATCH",
        body: { members: memberRecords(order) } }),
        `${nameOf(slug)} → position ${order.indexOf(slug) + 1} in ${lane.name}`);
    },
    join: (lane, slug, from) => dropOp(async () => {
      // leave first: a routine is in at most one lane and the store refuses the second claim
      if (from) await api(`/api/lanes/${from.id}`, { method: "PATCH",
        body: { members: memberRecords(from.members.filter((s) => s !== slug)) } });
      await api(`/api/lanes/${lane.id}`, { method: "PATCH",
        body: { members: [...memberRecords(lane.members), { slug }] } });
    }, `${nameOf(slug)} joined ${lane.name}`),
    leave: (lane, slug) => dropOp(() => api(`/api/lanes/${lane.id}`, { method: "PATCH",
      body: { members: memberRecords(lane.members.filter((s) => s !== slug)) } }),
      `${nameOf(slug)} left ${lane.name}`),
    reschedule: (slug, when) => {
      const spec = specAtInstant(cronToFriendly(cards.find((c) => c.slug === slug)?.cron), when, serverTz);
      if (!spec) { toast("custom schedule — edit it on the routine page", 4000, { error: true }); return; }
      return dropOp(() => api(`/api/routines/${slug}`, { method: "PATCH",
        body: { schedule: { friendly: spec } } }), `${nameOf(slug)} → ${fmtFireAt.format(when)}`);
    },
    rescheduleLane: (lane, when) => {
      const spec = specAtInstant(cronToFriendly(lane.cron), when, serverTz);
      if (!spec) { toast("custom lane schedule — edit it in the lane editor", 4000, { error: true }); return; }
      return dropOp(() => api(`/api/lanes/${lane.id}`, { method: "PATCH",
        body: { schedule: { friendly: spec } } }), `lane ${lane.name} → ${fmtFireAt.format(when)}`);
    },
  };
  const week = weekGrid(dragHandlers);
  const weekPanel = el("details", { class: "panel weekpanel",
    ...(storage.get(WEEK_KEY) !== "closed" ? { open: true } : {}) },
    el("summary", {}, "this week"), week.node);
  weekPanel.addEventListener("toggle", () => storage.set(WEEK_KEY, weekPanel.open ? "open" : "closed"));
  const filterBar = el("div", { class: "filterbar" });
  // D80: this page IS the lane-management surface — the bar carries "＋ new lane" + the
  // instance default; per-lane controls sit on the lane rows below. Rebuilt only when the
  // lanes payload changes (its select must survive live refreshes, the F229 rule); the
  // editors are overlays for the same reason.
  const lanesBar = el("div", { class: "panel mt", style: "padding:8px 12px" });
  const body = el("div", { class: "mt" });
  // The other axis (docs/lanes-domains.md): what a set of routines SHARES — one config block,
  // one store, one notes boundary. Its own section rather than a column on the table, because
  // a domain has nothing to do with when anything fires. OPEN by default: config nobody ever
  // looks at is config that drifts, where holding ONE copy of what its members would otherwise
  // each carry is the domain's whole job. Each domain's editor is heavy (it mounts the routine
  // page's own controls), so it builds only when someone asks for that one.
  // CLOSED by default, like the activity feed below it: six domains' worth of edit / rename /
  // delete sat above the routine list, so the page opened on an administration surface for a
  // thing most visits never touch. Its own head still names it, and the choice is remembered.
  const domainsPanel = el("details", { class: "panel weekpanel mt", "data-domains": "",
    ...(storage.get(DOMAINS_KEY) === "open" ? { open: true } : {}) },
    el("summary", {}, "domains — the config, secrets and store a set of routines shares"));
  const domainsView = domainsSection(domainsPanel, { reload: () => load() });
  domainsPanel.append(domainsView.body);
  domainsPanel.addEventListener("toggle",
    () => storage.set(DOMAINS_KEY, domainsPanel.open ? "open" : "closed"));
  // The cross-routine activity feed (the former Log page): every run, filterable, with the
  // transcript tailing inline. Collapsed by default and lazily started — a closed section
  // neither fetches nor polls.
  // `isOpen` is what keeps the section lazy after its FIRST open: start() arms the feed once
  // and it then polls four endpoints every 4 s, so re-collapsing has to stop it again.
  const feed = activityFeed({ isOpen: () => activityPanel.open });
  const activityPanel = el("details", { class: "panel weekpanel mt activity-panel",
    ...(storage.get(ACTIVITY_KEY) === "open" ? { open: true } : {}) },
    el("summary", {}, "activity — every run across every routine"), feed.node);
  activityPanel.addEventListener("toggle", () => {
    storage.set(ACTIVITY_KEY, activityPanel.open ? "open" : "closed");
    if (activityPanel.open) feed.start();
  });
  if (activityPanel.open) feed.start();
  view.append(banner, weekPanel, filterBar, lanesBar, body, domainsPanel, activityPanel);
  body.append(skeleton(), skeleton(), skeleton());

  let cards = [], llmReady = true, firesBySlug = new Map(), oneShotsBySlug = new Map();
  let serverTz = "";   // the zone crons are stored in — drag-reschedules re-time specs in it
  let laneData = null;   // the raw /api/lanes payload — the lane-management surface's input
  // slug -> its lane record. ONE record, not a list: a routine belongs to at most one lane and
  // `lanes.py` enforces it, so a badge, a row's real schedule and a chip all read the same
  // single answer (R107/F269 put the badges here).
  let laneBySlug = new Map();
  let lanesById = new Map();   // id -> the raw record (the lane rows' editor input)
  let lanesOrdered = [];   // [{id, name, members(slugs), …}] in fire order (F271)
  let domains = [];   // the /api/domains records, each already carrying its resolved members
  let domainBySlug = new Map();   // slug -> its domain record (at most one, by the same rule)
  let lastTagSig = null;   // F229: only rebuild the filter bar when the tag set changes
  let lastLaneSig = null;  // same rule for the lanes bar: its select must survive refreshes
  let domainsSeen = false;   // the domains payload has arrived at least once
  const states = new Set();
  // D72: the table IS the default (operator, 2026-08-05) — denser, sortable, and where the
  // lane rows live. The card grid stays one toggle away and a user's choice persists.
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


  // The card / table renderers live in dashboard-rows.js and read this page's state through
  // getters — the maps below are REPLACED by every load(), so a captured reference would draw
  // the state before the refresh.
  const rows = routineRows({
    llmReady: () => llmReady,
    laneFor: (slug) => laneBySlug.get(slug),
    domainFor: (slug) => domainBySlug.get(slug),
    laneRecord: (id) => lanesById.get(id),
    lanesOrdered: () => lanesOrdered,
    laneData: () => laneData,
    reload: () => load(),
    repaint: () => renderBody(),
    revealDomain: (id) => domainsView.reveal(id),
    // F208: re-clicking the active column reverses it; a new column starts at its natural
    // direction. The rule lives with the sort STATE, which is this page's, not a renderer's.
    sortArrow: (key) => (key === sortKey
      ? ((sortDir || (SORTS[key]?.[2] ? "desc" : "asc")) === "desc" ? " ▾" : " ▴") : ""),
    onSort: (key) => {
      if (key === sortKey) {
        const cur = sortDir || (SORTS[key]?.[2] ? "desc" : "asc");
        sortDir = cur === "desc" ? "asc" : "desc";
      } else {
        sortKey = key; sortDir = "";
      }
      storage.set(SORT_KEY, sortKey); storage.set(DIR_KEY, sortDir);
      renderFilterBar(); renderBody();
    },
  });

  function renderBody() {
    const shown = ordered(cards.filter(visible));
    week.update(cards.filter(visible), firesBySlug, oneShotsBySlug, lanesOrdered);
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
    if (viewMode === "list") { body.append(rows.table(shown)); return; }
    const grid = el("div", { class: "grid" });
    for (const c of shown) grid.append(rows.card(c));
    body.append(grid);
  }

  // Run events change routine cards, daemon status and live lane progress.
  // Domains and the week strip remain cached on light refreshes; /api/lanes also
  // carries the chain cursor and must refresh on run transitions. Refetching domains per bus tick
  // was the storm: /api/domains alone cost the daemon 4 s of parsing per request and was
  // requested every 600 ms while runs were active (2026-09-12, 249 calls averaging 67 s).
  let lastSched, lastDomainData, lastFullLoadAt = 0;
  async function load({ light = false } = {}) {
    let routines, status, sched, domainData;
    try {
      if (light && lastSched !== undefined && lastDomainData !== undefined && laneData) {
        [routines, status, laneData] = await Promise.all([
          api("/api/routines"), api("/api/status").catch(() => ({})),
          api("/api/lanes").catch(() => null)]);
        sched = lastSched;
        domainData = lastDomainData;
      } else {
        [routines, status, sched, laneData, domainData] = await Promise.all([
          api("/api/routines"), api("/api/status").catch(() => ({})),
          api("/api/schedule/week").catch(() => null),
          // Lane and domain membership are a nicety on this page — a hiccup on either fetch
          // must never blank the routines list, so each degrades to "none" rather than
          // throwing (R107, F269).
          api("/api/lanes").catch(() => null),
          api("/api/domains").catch(() => null),
        ]);
        lastSched = sched;
        lastDomainData = domainData;
        lastFullLoadAt = Date.now();
      }
    } catch (err) {
      body.replaceChildren(emptyState("✕", "Couldn't reach the daemon", err.message));
      return;
    }
    cards = routines;
    serverTz = laneData?.server_tz || "";
    // Members are RECORDS {slug} in the store; the display list keeps plain slugs (what the
    // week grid + rows consume). `fires` are the LANE's cron fire times from the week payload
    // (D71) — a scheduled lane's members carry no fires of their own, the chain is drawn from
    // these.
    const laneFires = new Map((sched?.lanes || [])
      .map((l) => [l.id, l.fires.map((t) => +new Date(t))]));
    lanesById = new Map((laneData?.lanes || []).map((l) => [l.id, l]));
    lanesOrdered = (laneData?.lanes || [])
      .map((l) => ({ id: l.id, name: l.name,
                     members: (l.members || []).map((m) => m.slug),
                     schedule_desc: l.schedule_desc || "", cron: l.cron || "",
                     paused: !!l.paused, fires: laneFires.get(l.id) || [] }));
    // slug -> its lane. One pass, one answer: the store refuses a second claim on a routine,
    // so the row's chip, its real schedule (R313 — a scheduled lane suppresses the member's
    // own cron; rendering that vestigial cron read as a lie) and the week strip all agree.
    laneBySlug = new Map();
    for (const l of laneData?.lanes || []) {
      for (const m of l.members || []) laneBySlug.set(m.slug, l);
    }
    // The domain list arrives with its members already resolved from the routines that name
    // it, so nothing here has to join two payloads to know who is in one.
    if (domainData) {
      domains = domainData.domains || [];
      domainBySlug = new Map();
      for (const d of domains) for (const slug of d.members || []) domainBySlug.set(slug, d);
      // Same only-on-change rule as the bars, for a stronger reason: this section can hold an
      // OPEN config editor; a rebuild on every bus tick would close it under the operator.
      if (domainsView.changed(domains)) domainsView.render(domains);
      domainsSeen = true;
    }
    domainsPanel.hidden = !domainsSeen;   // nothing fetched yet — claim nothing
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
        "active routines pause after their current turn; no scheduled, triggered or one-shot runs fire. Resume releases this global hold, not individual pauses. “▶ run now” still works. "),
      el("button", { class: "btn small primary", onclick: async (e) => {
        e.target.disabled = true;
        try { await api("/api/settings/pause", { method: "DELETE" }); toast("scheduling resumed"); await load(); }
        catch (err) { toastError(err); e.target.disabled = false; }
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
    // The lanes bar follows the same only-on-change rule (its select must survive live
    // refreshes); in_flight is deliberately OUT of the signature — chain progress renders
    // on the lane rows, not here.
    // Membership IS in the signature: the toolbar's create form offers only routines no lane
    // has claimed; a stale capture there would offer one that can only come back a 400.
    const laneSig = laneData
      ? JSON.stringify([laneData.default_on_failure, laneData.known_routines,
                        (laneData.lanes || []).map((l) => (l.members || []).map((m) => m.slug))])
      : null;
    if (laneSig !== lastLaneSig) {
      lastLaneSig = laneSig;
      lanesBar.replaceChildren();
      lanesBar.hidden = !laneData;
      if (laneData) lanesBar.append(lanesToolbar(laneData, { reload: load }));
    }
    renderBody();
  }

  await load();
  let pending = null;
  const LIGHT = new Set(["run_started", "run_finished", "run_state"]);
  // A reconnect means "anything may have moved while the stream was down" — but during a
  // daemon restart the bus reconnects on capped backoff, and a FULL load re-runs this page's
  // two heaviest reads (/api/schedule/week, /api/domains) exactly while the daemon is
  // coldest. Config-shaped state does not move minute to minute, so a reconnect reloads in
  // full only when the last full read is genuinely old; otherwise it catches up on run state
  // like any other run event.
  const FULL_MAX_AGE_MS = 5 * 60_000;
  const onBus = (e) => {
    const kind = e?.detail?.event;
    // llm_task / llm_process change nothing here (the LLM dock renders them); an answered
    // question is the badge's business
    if (kind === "llm_task" || kind === "llm_process" || kind === "question_answered") return;
    const light = LIGHT.has(kind)
      || (kind === "reconnect" && Date.now() - lastFullLoadAt < FULL_MAX_AGE_MS);
    clearTimeout(pending);
    pending = setTimeout(() => load({ light }).catch(() => {}), 2000);
  };
  window.addEventListener("rsched-bus", onBus);
  return () => {
    window.removeEventListener("rsched-bus", onBus);
    clearTimeout(pending);
    feed.dispose();
  };
}
