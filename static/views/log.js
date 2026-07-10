// Log: a live, filterable activity feed across all routines. Each row is a run; expand it to
// tail (or replay) that run's transcript inline. Live rows tail through stream.js liveTail,
// so a dropped stream reconnects with backoff instead of going quietly stale.

import { api } from "/static/api.js";
import { setQuery } from "/static/router.js";
import { liveTail } from "/static/stream.js";
import { createTranscript } from "/static/components/transcript.js";
import { chip, el, emptyState, fmtDur, skeleton, toDate, when } from "/static/util.js";

const WINDOWS = { "24h": 86400, "7d": 604800, "30d": 2592000, all: Infinity };
const TERMINAL = new Set(["finished", "failed", "aborted"]);
const isActive = (state) => !TERMINAL.has(state);

const withinS = (ts, secs) => {
  const t = toDate(ts);
  return t != null && (Date.now() - t.getTime()) / 1000 <= secs;
};

const kfmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n || 0));
const compactTokens = (u) => (u && (u.in || u.out)) ? `${kfmt(u.in || 0)}/${kfmt(u.out || 0)} tok` : "";
function runDuration(r) {
  const start = toDate(r.ts);
  if (!start) return "";
  const end = TERMINAL.has(r.state) ? (r.updated ? Date.parse(r.updated) : null) : Date.now();
  return end && end >= start.getTime() ? fmtDur((end - start.getTime()) / 1000) : "";
}

export async function render(view, query = {}) {
  // Filters + expanded rows live in the URL query, so the feed is shareable and survives reload.
  const DEFAULT_WINDOW = "7d";
  const filters = {
    routine: query.routine || "", status: query.status || "",
    window: query.window || DEFAULT_WINDOW, search: query.search || "", live: true };
  const expandedIds = new Set((query.expand || "").split(",").filter(Boolean));
  const syncURL = () => setQuery({
    routine: filters.routine, status: filters.status,
    window: filters.window === DEFAULT_WINDOW ? "" : filters.window,
    search: filters.search, expand: [...expandedIds].join(",") });
  const rows = new Map();          // run_id -> row controller (persists across refreshes)
  let allRuns = [], routineMeta = {}, statusData = { active_runs: {} }, questions = [];
  let optionsBuilt = false, loaded = false;

  // ---- header --------------------------------------------------------------
  const liveDot = el("span", { class: "lamp", title: "daemon link" });
  const refreshBtn = el("button", { class: "btn small" }, "↻ refresh");
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / activity"),
      el("h1", {}, "Log"),
      el("div", { class: "sub" }, "every run across every routine — live")),
    el("div", { class: "row" }, refreshBtn,
      el("span", { class: "row", style: "gap:6px" }, liveDot,
        el("span", { class: "faint small" }, "daemon")))));

  // ---- stats strip ---------------------------------------------------------
  const stats = el("div", { class: "stats" });
  view.append(stats);

  // ---- filter bar ----------------------------------------------------------
  const routineSel = el("select", {}, el("option", { value: "" }, "All routines"));
  const statusSel = el("select", {}, ...[
    ["", "All statuses"], ["running", "Running"], ["waiting_user", "Waiting"],
    ["finished", "Finished"], ["failed", "Failed"], ["aborted", "Aborted"],
  ].map(([v, l]) => el("option", { value: v }, l)));
  statusSel.value = filters.status;
  const windowSel = el("select", {}, ...Object.entries({
    "24h": "Last 24h", "7d": "Last 7 days", "30d": "Last 30 days", all: "All time",
  }).map(([v, l]) => el("option", { value: v, ...(v === filters.window ? { selected: true } : {}) }, l)));
  const searchInp = el("input", { type: "text", class: "search", value: filters.search,
    placeholder: "search routine · run · summary…" });
  const liveChk = el("input", { type: "checkbox", checked: true });

  routineSel.onchange = () => { filters.routine = routineSel.value; syncURL(); renderFeed(); };
  statusSel.onchange = () => { filters.status = statusSel.value; syncURL(); renderFeed(); };
  windowSel.onchange = () => { filters.window = windowSel.value; syncURL(); renderStats(); renderFeed(); };
  searchInp.oninput = () => { filters.search = searchInp.value.trim().toLowerCase(); syncURL(); renderFeed(); };
  liveChk.onchange = () => { filters.live = liveChk.checked; };

  view.append(el("div", { class: "logbar" },
    routineSel, statusSel, windowSel, searchInp,
    el("label", { class: "row", style: "gap:6px;margin:0" }, liveChk,
      el("span", { class: "faint small" }, "live-follow"))));

  const feed = el("div", { class: "feed" });
  feed.append(skeleton(), skeleton());
  view.append(feed);

  function setStatusFilter(v) { filters.status = v; statusSel.value = v; syncURL(); renderFeed(); }

  // ---- data ----------------------------------------------------------------
  async function load() {
    try {
      const [routines, runs, status, qs] = await Promise.all([
        api("/api/routines"),
        api("/api/runs?limit=300"),
        api("/api/status"),
        api("/api/questions").catch(() => []),
      ]);
      routineMeta = Object.fromEntries(routines.map((r) => [r.slug, r]));
      allRuns = runs;
      statusData = status || { active_runs: {} };
      questions = qs || [];
      liveDot.classList.add("on");
      loaded = true;
      if (!optionsBuilt) buildRoutineOptions(routines);
      renderStats();
      renderFeed();
    } catch {
      liveDot.classList.remove("on");                 // stay quiet on transient poll failures
      if (!loaded) feed.replaceChildren(emptyState("✕", "Couldn't reach the daemon",
        "The feed retries automatically while this page is open."));
    }
  }

  function buildRoutineOptions(routines) {
    for (const r of [...routines].sort((a, b) => (a.name || a.slug).localeCompare(b.name || b.slug)))
      routineSel.append(el("option", { value: r.slug }, r.name || r.slug));
    routineSel.value = filters.routine;   // restore the URL's routine once its option exists
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
      statCard(questions.length, "open decisions", "warn", () => { location.hash = "#/questions"; }));
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
      if (expandedIds.has(r.run_id)) ctrl.ensureOpen();   // restore expansion from the URL
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
      const oneLine = (r.summary || "").split("\n").find((l) => l.trim())
        || (isActive(r.state) ? "…in progress" : "(no summary)");
      sumEl.textContent = oneLine;
      sumEl.title = r.summary || "";
    }

    async function build() {
      transcript = createTranscript(body);
      if (isActive(cur.state)) {
        tail = liveTail({
          page: (o) => `/api/runs/${r0.run_id}/transcript?offset=${o}`,
          events: (o) => `/api/runs/${r0.run_id}/events?offset=${o}`,
          onEvent: (ev) => transcript && transcript.add(ev),
        });
      } else {
        try {
          const { events } = await api(`/api/runs/${r0.run_id}/transcript`);
          if (!transcript) return;                           // collapsed while fetching
          if (!events.length) body.append(el("div", { class: "empty" },
            el("div", { class: "t" }, "empty transcript")));
          for (const ev of events) transcript.add(ev);
        } catch (err) {
          body.append(el("div", { class: "ev error" }, `couldn't load transcript: ${err.message}`));
        }
      }
    }

    function closeTail() { if (tail) { tail.stop(); tail = null; } }
    function doExpand() {
      if (expanded) return;
      expanded = true; rowEl.classList.add("open"); body.hidden = false;
      build();
    }
    function collapse() {
      expanded = false; transcript = null;
      rowEl.classList.remove("open"); body.hidden = true; body.replaceChildren();
      closeTail();
    }
    head.onclick = () => {
      if (expanded) { collapse(); expandedIds.delete(r0.run_id); }
      else { doExpand(); expandedIds.add(r0.run_id); }
      syncURL();
    };

    update(r0);
    return { el: rowEl, update, dispose: closeTail, ensureOpen: doExpand };
  }

  // ---- live wiring ---------------------------------------------------------
  let pending = null;
  const onBus = () => {
    if (!filters.live) return;
    clearTimeout(pending);
    pending = setTimeout(() => load(), 600);               // debounce bursts of bus events
  };
  window.addEventListener("rsched-bus", onBus);
  // poll while anything is active (bus fires on transitions, not every turn)
  const poll = setInterval(() => {
    if (filters.live && (Object.keys(statusData.active_runs || {}).length || allRuns.some((r) => isActive(r.state))))
      load();
  }, 4000);
  refreshBtn.onclick = () => load();

  await load();

  return () => {
    window.removeEventListener("rsched-bus", onBus);
    clearInterval(poll);
    clearTimeout(pending);
    for (const ctrl of rows.values()) ctrl.dispose();
  };
}
