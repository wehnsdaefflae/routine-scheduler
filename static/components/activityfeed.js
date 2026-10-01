// The live cross-routine run feed: one row per run, expand it to tail (or replay) that run's
// transcript inline. Live rows tail through stream.js liveTail, so a dropped stream reconnects
// with backoff instead of going quietly stale. This was the whole of the Log page until 0.106.0;
// it is now the Dashboard's activity section — mount it anywhere with activityFeed().
//
// The mount is LAZY: nothing loads or polls until start() is called (the Dashboard calls it when
// its section is open), so a collapsed section costs nothing — and it stays lazy AFTERWARDS.
// `isOpen()` is the same predicate shape createTaskTree takes: start() arms the wiring once,
// and every refresh the bus or the 4 s poll would make is skipped while the section is shut.
// Without it, one click open and one click closed left three fetches (routines, 300 runs,
// status) firing every four seconds for content nobody could see, for the life of the tab — the
// same "refetch nothing renders" class as any leaked poller.
//
// The open-decisions tile is a READER of questions-store.js, never a fetcher of its own: the
// header badge already keeps that one list fresh on every bus event, so a feed that asked for it
// with every 4 s refresh doubled the load on one of the two read models that starved the daemon.
// One load is in flight at a time, and a refresh asked for meanwhile runs once after it — a slow
// daemon used to collect a new batch of requests every poll, answered in whatever order.

import { api } from "/static/api.js";
import { summaryLine } from "/static/md.js";
import { loadQuestions, subscribeQuestions } from "/static/questions-store.js";
import { liveTail } from "/static/stream.js";
import { createTranscript } from "/static/components/transcript.js";
import { chip, el, emptyState, fmtDur, fmtNum, skeleton, storage, toDate, when } from "/static/util.js";
import { TERMINAL } from "/static/states.js";

const WINDOWS = { "24h": 86400, "7d": 604800, "30d": 2592000, all: Infinity };
const WINDOW_KEY = "rsched_activity_window";
const isActive = (state) => !TERMINAL.has(state);

const withinS = (ts, secs) => {
  const t = toDate(ts);
  return t != null && (Date.now() - t.getTime()) / 1000 <= secs;
};

const compactTokens = (u) => (u && (u.in || u.out)) ? `${fmtNum(u.in || 0)}/${fmtNum(u.out || 0)} tok` : "";
function runDuration(r) {
  const start = toDate(r.ts);
  if (!start) return "";
  const end = TERMINAL.has(r.state) ? (r.updated ? Date.parse(r.updated) : null) : Date.now();
  return end && end >= start.getTime() ? fmtDur((end - start.getTime()) / 1000) : "";
}

// Returns { node, start, dispose }. `start()` is idempotent: first call loads and wires the
// live plumbing (bus listener + poll while anything runs); later calls just refresh.
export function activityFeed({ isOpen = () => true } = {}) {
  const filters = { routine: "", status: "", window: storage.get(WINDOW_KEY) || "7d", search: "", live: true };
  const rows = new Map();          // run_id -> row controller (persists across refreshes)
  let allRuns = [], routineMeta = {}, statusData = { active_runs: {} };
  let openDecisions = 0;           // from questions-store.js — the badge's own definition of open
  let optionsBuilt = false, loaded = false, started = false;
  let pending = null, poll = null, unsubscribe = null;
  let inflight = null, again = false;   // one load at a time; `again` = one more after it
  let disposed = false;                 // the view is gone: a queued reload must not fire

  const stats = el("div", { class: "stats" });
  const routineSel = el("select", {}, el("option", { value: "" }, "All routines"));
  const statusSel = el("select", {}, ...[
    ["", "All statuses"], ["running", "Running"], ["waiting_user", "Waiting"],
    ["finished", "Finished"], ["failed", "Failed"], ["aborted", "Aborted"],
  ].map(([v, l]) => el("option", { value: v }, l)));
  const windowSel = el("select", {}, ...Object.entries({
    "24h": "Last 24h", "7d": "Last 7 days", "30d": "Last 30 days", all: "All time",
  }).map(([v, l]) => el("option", { value: v, ...(v === filters.window ? { selected: true } : {}) }, l)));
  const searchInp = el("input", { type: "text", class: "search",
    placeholder: "search routine · run · summary…" });
  const liveChk = el("input", { type: "checkbox", checked: true });
  const feed = el("div", { class: "feed" });

  routineSel.onchange = () => { filters.routine = routineSel.value; renderFeed(); };
  statusSel.onchange = () => { filters.status = statusSel.value; renderFeed(); };
  windowSel.onchange = () => {
    filters.window = windowSel.value; storage.set(WINDOW_KEY, filters.window);
    renderStats(); renderFeed();
  };
  searchInp.oninput = () => { filters.search = searchInp.value.trim().toLowerCase(); renderFeed(); };
  liveChk.onchange = () => { filters.live = liveChk.checked; };

  const node = el("div", {}, stats,
    el("div", { class: "logbar" }, routineSel, statusSel, windowSel, searchInp,
      el("label", { class: "row", style: "gap:6px;margin:0" }, liveChk,
        el("span", { class: "faint small" }, "live-follow"))),
    feed);
  feed.append(skeleton(), skeleton());

  function setStatusFilter(v) { filters.status = v; statusSel.value = v; renderFeed(); }

  // ---- data ----------------------------------------------------------------
  function load() {
    if (inflight) { again = true; return; }
    inflight = (async () => {
      try {
        const [routines, runs, status] = await Promise.all([
          api("/api/routines"),
          api("/api/runs?limit=300"),
          api("/api/status"),
        ]);
        routineMeta = Object.fromEntries(routines.map((r) => [r.slug, r]));
        allRuns = runs;
        statusData = status || { active_runs: {} };
        loaded = true;
        if (!optionsBuilt) buildRoutineOptions(routines);
        renderStats();
        renderFeed();
      } catch {
        if (!loaded) feed.replaceChildren(emptyState("✕", "Couldn't reach the daemon",
          "The feed retries automatically while this page is open."));
      }
    })().finally(() => {
      inflight = null;
      if (again) { again = false; if (!disposed && isOpen()) load(); }
    });
  }

  // "Open" is what the header badge counts — unanswered and not snoozed. The tile counted
  // snoozed decisions too, so a snooze the badge honoured still read as waiting here.
  function onQuestions({ items }) {
    openDecisions = items.filter((q) => !q.answered && !q.snoozed).length;
    if (loaded) renderStats();
  }

  function buildRoutineOptions(routines) {
    for (const r of [...routines].sort((a, b) => (a.name || a.slug).localeCompare(b.name || b.slug)))
      routineSel.append(el("option", { value: r.slug }, r.name || r.slug));
    routineSel.value = filters.routine;
    optionsBuilt = true;
  }

  // ---- stats ---------------------------------------------------------------
  function statCard(value, label, cls, onclick) {
    return el("div", { class: `stat ${cls}${onclick ? " click" : ""}`, ...(onclick ? { onclick } : {}) },
      el("div", { class: "v" }, String(value)),
      el("div", { class: "l" }, label));
  }
  function renderStats() {
    const win = WINDOWS[filters.window] ?? Infinity;
    const runningNow = Object.keys(statusData.active_runs || {}).length;
    const waiting = allRuns.filter((r) => r.state === "waiting_user").length;
    const failed = allRuns.filter((r) => r.state === "failed" && withinS(r.ts, 86400)).length;
    const inWindow = allRuns.filter((r) => win === Infinity || withinS(r.ts, win)).length;
    stats.replaceChildren(
      statCard(runningNow, "running now", "live", () => setStatusFilter("running")),
      statCard(waiting, "waiting on you", "warn", () => setStatusFilter("waiting_user")),
      statCard(failed, "failed · 24h", "err", () => setStatusFilter("failed")),
      statCard(inWindow, `runs · ${filters.window}`, "amber", () => setStatusFilter("")),
      statCard(openDecisions, "open decisions", "warn", () => { location.hash = "#/questions"; }));
  }

  // ---- feed ----------------------------------------------------------------
  function passesFilter(r) {
    if (filters.routine && r.routine !== filters.routine) return false;
    if (filters.status && r.state !== filters.status) return false;
    const win = WINDOWS[filters.window] ?? Infinity;
    if (win !== Infinity && !withinS(r.ts, win)) return false;
    if (filters.search &&
        !`${routineMeta[r.routine]?.name || ""} ${r.routine} ${r.run_id} ${r.summary || ""}`
          .toLowerCase().includes(filters.search)) return false;
    return true;
  }

  function renderFeed() {
    const list = allRuns.filter(passesFilter);
    const seen = new Set();
    const els = list.map((r) => {
      seen.add(r.run_id);
      let ctrl = rows.get(r.run_id);
      if (!ctrl) { ctrl = makeRow(r); rows.set(r.run_id, ctrl); }
      ctrl.update(r);
      return ctrl.el;
    });
    for (const [id, ctrl] of rows)
      if (!seen.has(id)) { ctrl.dispose(); rows.delete(id); }   // dropped by retention/filter
    if (els.length) feed.replaceChildren(...els);
    else feed.replaceChildren(allRuns.length
      ? emptyState("▢", "No runs match these filters", "Loosen the routine / status / window filters above.")
      : emptyState("◌", "No runs yet", "Nothing has executed. Fire one from a routine's “run now”."));
  }

  function makeRow(r0) {
    const stateChip = chip(r0.state, r0.state);
    const nameEl = el("span", { class: "rname" });
    const sumEl = el("span", { class: "rsum" });
    const metaEl = el("span", { class: "rmeta" });
    const openLink = el("a", { class: "btn small", href: `#/run/${r0.run_id}`,
      onclick: (e) => e.stopPropagation(), title: "open the full run view" }, "open ↗");
    const caret = el("span", { class: "caret" }, "▶");
    const head = el("div", { class: "rowhead" },
      stateChip,
      el("div", { class: "rleft" }, el("div", { class: "rline1" }, nameEl, metaEl), sumEl),
      el("div", { class: "rowright" }, openLink, caret));
    const body = el("div", { class: "logbody", hidden: true });
    const rowEl = el("div", { class: "logrow" }, head, body);

    let expanded = false, tail = null, transcript = null, cur = r0;

    function update(r) {
      cur = r;
      if (stateChip.textContent !== r.state) { stateChip.textContent = r.state; stateChip.className = `chip ${r.state}`; }
      nameEl.textContent = routineMeta[r.routine]?.name || r.routine;
      metaEl.replaceChildren(when(r.ts));
      const bits = [`${r.turn || 0} turns`, compactTokens(r.usage), runDuration(r)].filter(Boolean);
      if (bits.length) metaEl.append(`  ·  ${bits.join("  ·  ")}`);
      // The summary is MODEL PROSE. Assigned to `textContent` it kept its markers, so the
      // feed's one-line preview read "**Two applications w…" while the routine page and the
      // dashboard rendered the same field. One helper reduces it for all three.
      sumEl.replaceChildren(summaryLine(r.summary,
        isActive(r.state) ? "…in progress" : "(no summary)"));
      sumEl.title = r.summary || "";
    }

    async function build() {
      const mine = createTranscript(body, {
        fileUrl: (rel) => `/api/runs/${r0.run_id}/file?path=${encodeURIComponent(rel)}` });
      transcript = mine;
      if (isActive(cur.state)) {
        tail = liveTail({
          page: (o) => `/api/runs/${r0.run_id}/transcript?offset=${o}`,
          events: (o) => `/api/runs/${r0.run_id}/events?offset=${o}`,
          onEvent: (ev) => transcript && transcript.add(ev),
        });
      } else {
        // Collapsed while fetching — or collapsed AND re-opened, which builds a second
        // transcript: this fetch's events then belong to neither, and adding them to the new
        // one rendered every event twice.
        const stale = () => transcript !== mine;
        try {
          const { events } = await api(`/api/runs/${r0.run_id}/transcript`);
          if (stale()) return;
          if (!events.length) body.append(el("div", { class: "empty" },
            el("div", { class: "t" }, "empty transcript")));
          for (const ev of events) mine.add(ev);
        } catch (err) {
          if (stale()) return;
          body.append(el("div", { class: "ev error" }, `couldn't load transcript: ${err.message}`));
        }
      }
    }

    function closeTail() { if (tail) { tail.stop(); tail = null; } }
    head.onclick = () => {
      if (expanded) {
        expanded = false; transcript = null;
        rowEl.classList.remove("open"); body.hidden = true; body.replaceChildren();
        closeTail();
      } else {
        expanded = true; rowEl.classList.add("open"); body.hidden = false;
        build();
      }
    };

    update(r0);
    return { el: rowEl, update, dispose: closeTail };
  }

  // ---- live wiring ---------------------------------------------------------
  const onBus = (e) => {
    if (!filters.live || !isOpen()) return;
    // llm_task / llm_process events fire several times a second during a busy run and change
    // nothing this feed shows (the LLM dock renders them); a reload per event was four
    // fetches — routines, 300 runs, status, questions — every 600 ms per open tab, which is
    // how the daemon spent an afternoon answering nothing else (2026-09-12)
    const kind = e?.detail?.event;
    if (kind === "llm_task" || kind === "llm_process") return;
    clearTimeout(pending);
    pending = setTimeout(() => load(), 2000);              // debounce bursts of bus events
  };

  function start() {
    if (started) { load(); return; }
    started = true;
    window.addEventListener("rsched-bus", onBus);
    // poll while anything is active (bus fires on transitions, not every turn)
    poll = setInterval(() => {
      if (!isOpen()) return;
      if (filters.live && (Object.keys(statusData.active_runs || {}).length || allRuns.some((r) => isActive(r.state))))
        load();
    }, 4000);
    // the store's own cadence keeps the tile current from here on; this first load joins a
    // fetch already in flight and reaches the tile through the subscription
    unsubscribe = subscribeQuestions(onQuestions);
    loadQuestions().catch(() => { /* the daemon lamp reports the link; the tile stays 0 */ });
    load();
  }

  function dispose() {
    disposed = true;
    window.removeEventListener("rsched-bus", onBus);
    clearInterval(poll);
    clearTimeout(pending);
    unsubscribe?.();
    for (const ctrl of rows.values()) ctrl.dispose();
    rows.clear();
  }

  return { node, start, dispose };
}
