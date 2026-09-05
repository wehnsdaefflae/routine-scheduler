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
import { slugColor } from "/static/components/charts.js";
import { confirmDialog, promptDialog } from "/static/components/dialog.js";
import { domainConfigPanel } from "/static/components/domainconfig.js";
import { heartbeat } from "/static/components/heartbeat.js";
import { laneControls, laneProgress, lanesToolbar, openLaneEditor } from "/static/components/lanemanage.js";
import { quotaLine } from "/static/views/settings-endpoints.js";
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
const LANES_OPEN_KEY = "rsched_dash_lanes_open";
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

//: The claude-cli endpoints are the ones with a subscription behind them. The quota is per
//: ACCOUNT, so the FIRST one answers for all of them — asking each would print the same numbers
//: twice. Silent on every failure: this is a convenience chip, not a status the page depends on.
async function loadQuota(chip) {
  try {
    const eps = (await api("/api/settings/endpoints")).endpoints || [];
    const cli = eps.find((e) => e.kind === "claude-cli");
    if (!cli) return;
    const q = await api(`/api/settings/endpoints/${encodeURIComponent(cli.name)}/quota`);
    if (!q.supported) return;
    if (!q.ok) {
      chip.className = "chip disabled";
      chip.title = q.error || "";
      chip.textContent = "subscription quota unavailable";
      chip.hidden = false;
      return;
    }
    const lowest = Math.min(...Object.values(q.windows).map((w) => w.remaining));
    chip.className = `chip ${lowest <= 10 ? "blocking" : lowest <= 25 ? "partial" : "idle"}`;
    chip.title = "Claude subscription — the whole account, including your interactive sessions";
    chip.textContent = quotaLine(q);
    chip.hidden = false;
  } catch { /* a chip that cannot load is simply not shown */ }
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
  // The subscription's remaining quota, on the page the operator opens. It lived only on a
  // Settings endpoint card as muted 11px text, which is why the answer to "why don't I see it"
  // was partly "you would have had to go looking". SIGNAL while there is room, SUMMONS when a
  // window is nearly spent — an exhausted quota is a thing that waits on a person.
  const quotaChip = el("span", { class: "chip bare", hidden: true });
  view.append(
    el("div", { class: "page-head" },
      el("div", {},
        el("h1", {}, "Routines")),
      el("div", { class: "row" }, quotaChip, pauseBtn)));
  loadQuota(quotaChip);
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
    try { await fn(); toast(okMsg); } catch (err) { toast(err.message, 4000, { error: true }); }
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
  const domainsBody = el("div", {});
  const domainsPanel = el("details", { class: "panel weekpanel mt", "data-domains": "",
    ...(storage.get(DOMAINS_KEY) !== "closed" ? { open: true } : {}) },
    el("summary", {}, "domains — the config, secrets and store a set of routines shares"),
    domainsBody);
  domainsPanel.addEventListener("toggle",
    () => storage.set(DOMAINS_KEY, domainsPanel.open ? "open" : "closed"));
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
  let lastDomainSig = null;   // and for the domains section: it holds open config editors
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

  // ---- the domains section ---------------------------------------------------------------
  // Membership is NOT editable here; saying so is half the section's job. A routine names
  // its domain in its own routine.yaml, so joining and leaving are an ordinary config save on
  // the routine's page. That is what keeps "at most one domain" a fact of the file — and it is
  // the first thing someone looks for on this page, so the line is not buried in a tooltip.
  const domainSig = () => JSON.stringify(domains);
  // "library-sync · self-audit · 4 shared settings" — who is in it and how much it hands them,
  // which is the pair that decides whether a domain is doing anything.
  const domainSummary = (d) => {
    const members = d.members || [];
    const n = Object.keys(d.config || {}).length;
    return `${members.length ? members.join(" · ") : "no members"}`
      + ` · ${n} shared setting${n === 1 ? "" : "s"}`;
  };

  function domainRow(d) {
    const counts = el("span", { class: "faint small" }, domainSummary(d));
    const host = el("div", { class: "mt", hidden: true });
    let built = false;
    // The saved record REPLACES the one this row was built from; the signature moves with it
    // too, or the next bus tick would find "changed" data and tear down an open editor.
    const onSaved = (rec) => {
      const at = domains.findIndex((x) => x.id === rec.id);
      if (at >= 0) domains[at] = rec;
      counts.textContent = domainSummary(rec);
      lastDomainSig = domainSig();
    };
    const edit = el("button", { class: "btn small ghost", "data-domain-edit": "",
      title: "edit what this domain shares: permissions, rules, secrets, connections, roots" },
      "✎ edit");
    edit.onclick = () => {
      if (!built) { built = true; host.append(domainConfigPanel(d, { onSaved })); }
      host.hidden = !host.hidden;
    };
    const ren = el("button", { class: "btn small ghost", "data-domain-rename": "" }, "rename");
    ren.onclick = async () => {
      const name = await promptDialog(`Rename domain “${d.name}”`, { value: d.name });
      if (!name || name === d.name) return;
      try { await api(`/api/domains/${d.id}`, { method: "PATCH", body: { name } });
        toast(`domain renamed to “${name}”`); }
      catch (ex) { toast(ex.message, 4000, { error: true }); }
      await load();
    };
    // A domain with members cannot be deleted (409) — every one of them would be left naming
    // nothing and silently narrowed. The server says exactly who is holding it; say that back.
    const del = el("button", { class: "btn small danger", "data-domain-delete": "" }, "delete");
    del.onclick = async () => {
      if (!(await confirmDialog(`Delete domain “${d.name}”? The shared store on disk is kept.`,
        { confirmLabel: "delete" }))) return;
      try { await api(`/api/domains/${d.id}`, { method: "DELETE" }); toast("domain deleted"); }
      catch (ex) { toast(ex.message, 6000, { error: true }); }
      await load();
    };
    return el("div", { class: "mt", "data-domain-row": d.id },
      el("div", { class: "row", style: "gap:8px;align-items:center;flex-wrap:wrap" },
        el("span", {}, `◈ ${d.name}`), counts,
        el("span", { class: "row", style: "gap:6px;margin-left:auto" }, edit, ren, del)),
      host);
  }

  function renderDomains() {
    domainsBody.replaceChildren();
    const add = el("button", { class: "btn small", "data-domain-new": "" }, "＋ new domain");
    add.onclick = async () => {
      const name = await promptDialog("Name the new domain",
        { placeholder: "what these routines have in common" });
      if (!name) return;
      try { await api("/api/domains", { method: "POST", body: { name } });
        toast(`domain “${name}” added`); }
      catch (ex) { toast(ex.message, 4000, { error: true }); }
      await load();
    };
    domainsBody.append(
      el("div", { class: "row", style: "gap:8px;align-items:center;flex-wrap:wrap" },
        el("span", { class: "lbl" }, "◈ domains"), add,
        el("span", { class: "muted small" },
          "a routine JOINS a domain on its own page — the domain setting in its config. "
          + "At most one — it is what puts the shared store in the run's roots.")),
      ...(domains.length
        ? domains.map(domainRow)
        : [el("div", { class: "muted small mt" },
            "No domains yet. Make one when two routines should share a permission surface, a "
            + "secret and a store — routines that only need to fire in order want a lane.")]));
  }

  // Where a routine's domain chip goes: open the section (it may be collapsed) and bring that
  // domain's row into view. The rows are built on every load regardless of the panel's state,
  // so the target is always there to scroll to.
  function revealDomain(id) {
    domainsPanel.open = true;
    domainsBody.querySelector(`[data-domain-row="${CSS.escape(id)}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

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
    if (viewMode === "list") { body.append(table(shown)); return; }
    const grid = el("div", { class: "grid" });
    for (const c of shown) grid.append(card(c));
    body.append(grid);
  }

  async function load() {
    let routines, status, sched, domainData;
    try {
      [routines, status, sched, laneData, domainData] = await Promise.all([
        api("/api/routines"), api("/api/status").catch(() => ({})),
        api("/api/schedule/week").catch(() => null),
        // Lane and domain membership are a nicety on this page — a hiccup on either fetch must
        // never blank the routines list, so each degrades to "none" rather than throwing
        // (R107, F269).
        api("/api/lanes").catch(() => null),
        api("/api/domains").catch(() => null),
      ]);
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
      const sig = domainSig();
      // Same only-on-change rule as the bars, for a stronger reason: this section can hold an
      // OPEN config editor; a rebuild on every bus tick would close it under the operator.
      if (sig !== lastDomainSig) { lastDomainSig = sig; renderDomains(); }
    }
    domainsPanel.hidden = lastDomainSig === null;   // nothing fetched yet — claim nothing
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

  // The schedule a routine will ACTUALLY fire on. A member of a SCHEDULED lane has its own
  // cron suppressed by the daemon — showing that vestigial cron here read as a lie (R313),
  // so a lane-driven row shows the lane's schedule instead. An unscheduled lane suppresses
  // nothing, so its members keep showing their own.
  function schedText(c) {
    const lane = laneBySlug.get(c.slug);
    if (!lane?.cron) return null;   // the caller renders the routine's own desc
    return `⛓ ${lane.name} — ${lane.paused ? "lane paused" : (lane.schedule_desc || "scheduled")}`;
  }

  // The two structures a routine sits in, as one chip row: its LANE (when it fires and with
  // whom) and its DOMAIN (what it shares). They are independent, so the row shows whichever a
  // routine has — side by side when it has both. Each chip goes where that structure is
  // edited: the lane chip opens the lane's editor (D80: this page is the lane-management
  // surface), the domain chip reveals its row in the section below. Both LOOK clickable, so
  // both must be — a chip beside a working one that does nothing teaches the wrong thing.
  // Membership itself is not on either: joining a domain is a save on the routine's own page.
  function structureChips(slug) {
    const lane = laneBySlug.get(slug);
    const dom = domainBySlug.get(slug);
    if (!lane && !dom) return null;
    return el("div", { class: "lanes-row" },
      lane ? el("button", { class: "chip lane-chip",
        title: `runs in lane “${lane.name}” — edit the lane`,
        onclick: (e) => { e.stopPropagation(); openLaneEditor(lane, laneData, { reload: load }); },
      }, `⛓ ${lane.name}`) : null,
      dom ? el("button", { class: "chip domain-chip",
        title: `shares config, secrets and a store with the “${dom.name}” domain — open it in `
             + "the Domains section; this routine's own page is where it joined",
        onclick: (e) => { e.stopPropagation(); revealDomain(dom.id); },
      }, `◈ ${dom.name}`) : null);
  }

  function card(c) {
    const stateChip = c.active_state ? chip(c.active_state, c.active_state)
      : c.retired ? chip("finished", "finished")
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
      structureChips(c.slug),
      c.description ? el("div", { class: "desc" }, c.description) : null,
      blocked ? el("div", { class: "qflag" },
        el("span", {}, "waiting on your answer"),
        el("a", { class: "btn small primary", href: "#/questions", style: "margin-left:auto" }, "decide")) : null,
      el("div", { class: "meta" },
        el("span", schedText(c) ? { title: "lane-driven — the lane's schedule fires this routine; its own cron is suppressed" } : {},
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
    // U-order (user, 2026-08-13): inside an expanded lane the row order IS the fire
    // order, so the rows themselves are the reorder surface — drag one onto a sibling
    // (upper half = before it, lower half = after). The editor's ↑/↓ stays for precision.
    let dragFrom = null;
    const rowFor = (c, extraCls = "", lane = null) => {
      const last = c.last_run;
      const rowCls = [RUNNING.has(c.active_state) ? "live" : "",
        c.active_state === "waiting_user" ? "attention" : "",
        c.enabled && !c.retired ? "" : "disabled-row", extraCls]
        .filter(Boolean).join(" ");
      const stats = statsLine(last);
      const tr = el("tr", { class: rowCls },
        el("td", {}, swatch(c.slug), el("a", { href: `#/routine/${c.slug}` }, c.name || c.slug),
          c.open_questions ? el("a", { href: "#/questions", class: "chip blocking",
            title: "open questions waiting for you" }, `${c.open_questions} open ?`) : null,
          c.description ? el("div", { class: "faint small", style: "max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, c.description) : null,
          structureChips(c.slug)),
        el("td", { class: "hb-cell" }, c.recent_runs?.length
          ? heartbeat(c.recent_runs) : el("span", { class: "faint" }, "—")),
        el("td", { class: "muted small" },
          el("div", schedText(c)
            ? { title: "lane-driven — the lane's schedule fires this routine; its own cron is suppressed" }
            : {},
            // an always-visible marker: the row dim alone was too subtle. FINISHED and OFF are
            // different answers to "why is nothing happening" — one is the job being over.
            c.retired
              ? el("span", { class: "chip finished", style: "margin-right:6px",
                  title: "every final-goal condition is met — this routine is done and no "
                       + "longer fires. Reopen a goal condition to bring it back." }, "done")
              : c.enabled ? null : el("span", { class: "chip disabled", style: "margin-right:6px",
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
      if (lane) {
        tr.draggable = true;
        tr.dataset.dragMember = c.slug;
        tr.ondragstart = (e) => {
          dragFrom = { lid: lane.id, slug: c.slug };
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", c.slug);
        };
        tr.ondragover = (e) => {
          if (!dragFrom || dragFrom.lid !== lane.id || dragFrom.slug === c.slug) return;
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
          if (!from || from.lid !== lane.id || from.slug === c.slug) return;
          const raw = lanesById.get(lane.id);
          const rec = raw?.members?.find((m) => m.slug === from.slug);
          if (!rec) return;
          const list = raw.members.filter((m) => m.slug !== from.slug);
          const at = list.findIndex((m) => m.slug === c.slug);
          if (at < 0) return;
          const box = tr.getBoundingClientRect();
          const before = e.clientY < box.top + box.height / 2;
          list.splice(at + (before ? 0 : 1), 0, rec);
          try {
            await api(`/api/lanes/${lane.id}`, { method: "PATCH", body: { members: list } });
            toast(`“${lane.name}” fire order: ${list.map((m) => m.slug).join(" → ")}`);
          } catch (ex) { toast(ex.message, 4000, { error: true }); }
          load();
        };
      }
      return tr;
    };
    // D73 + F281: each lane is its own collapsible row — expanding lists its members right
    // beneath it, in the lane's FIRE order (not the table sort). A routine in a lane lives
    // ONLY under that lane's row (reviewer order 2026-08-06: the flat list used to repeat
    // every member, so the table double-listed them); the flat sorted list below carries just
    // the routines no lane claims. Expansion persists like the view mode, keyed by lane ID —
    // a rename must not silently collapse the row.
    const openLanes = new Set(JSON.parse(storage.get(LANES_OPEN_KEY) || "[]"));
    const bySlug = new Map(shown.map((c) => [c.slug, c]));
    const rows = [];
    for (const lane of lanesOrdered) {
      const members = lane.members.map((s) => bySlug.get(s)).filter(Boolean);
      // A lane whose members are all filtered out loses its header too — that is the filter
      // working. An EMPTY lane keeps its row: this row is the only way back into its editor —
      // a lane created ahead of its members would otherwise vanish the moment it was made.
      if (lane.members.length && !members.length) continue;
      const open = openLanes.has(lane.id);
      const raw = lanesById.get(lane.id);
      // D80: the lane row carries its management — run now / pause / edit (the buttons
      // stopPropagation so the row's expand toggle keeps working), plus how far an in-flight
      // chain has got: which member of how many is running.
      const controls = raw ? laneControls(raw, laneData, { reload: load }) : [];
      const progress = raw ? laneProgress(raw, laneData) : null;
      rows.push(el("tr", { class: "lane-row", "data-lane-row": lane.id },
        el("td", { colSpan: COLS.length,
          title: open ? "collapse this lane's rows" : "expand this lane's member rows",
          onclick: () => {
            open ? openLanes.delete(lane.id) : openLanes.add(lane.id);
            storage.set(LANES_OPEN_KEY, JSON.stringify([...openLanes]));
            renderBody();
          } },
          el("div", { class: "row", style: "justify-content:space-between;align-items:center;gap:8px" },
            el("span", {},
              el("span", { class: "tri" }, open ? "▾ " : "▸ "),
              `⛓ ${lane.name}`,
              lane.paused ? el("span", { class: "muted small", "data-lane-paused": "",
                style: "margin-left:8px" }, "⏸ paused") : null,
              el("span", { class: "faint small", style: "margin-left:8px" },
                `${members.length} routine${members.length === 1 ? "" : "s"} · fire order`
                + (lane.cron ? ` · ${lane.paused ? "paused" : lane.schedule_desc}` : "")),
              progress ? el("span", { style: "margin-left:8px" }, progress) : null),
            el("span", { class: "row", style: "gap:6px" }, ...controls)))));
      if (open) for (const m of members) rows.push(rowFor(m, "lane-member", lane));
    }
    const inLane = new Set(lanesOrdered.flatMap((l) => l.members));
    for (const c of shown) if (!inLane.has(c.slug)) rows.push(rowFor(c));
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
