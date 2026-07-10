// Log: a live, filterable activity feed across all routines. Each row is a run;
// expand it to tail (or replay) that run's transcript inline. Reuses the shared
// transcript renderer, the global bus, and /api/runs + /api/status + /api/questions.

import { api, sse } from "/static/api.js";
import { setQuery } from "/static/router.js";
import { createTranscript } from "/static/components/transcript.js";
import { chip, el, fmtTs } from "/static/util.js";

const WINDOWS = { "24h": 86400, "7d": 604800, "30d": 2592000, all: Infinity };
const TERMINAL = new Set(["finished", "failed", "aborted"]);
const isActive = (state) => !TERMINAL.has(state);

// "20260708-220004" → epoch ms (parsed in local time; good enough for windowing/duration).
function parseTs(ts) {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(ts || "");
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() : null;
}
const withinS = (ts, secs) => { const t = parseTs(ts); return t != null && (Date.now() - t) / 1000 <= secs; };

function fmtDur(secs) {
  if (secs == null || secs < 0) return "";
  if (secs < 60) return `${Math.round(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
  return `${Math.floor(secs / 3600)}h ${Math.round((secs % 3600) / 60)}m`;
}
const kfmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n || 0));
const compactTokens = (u) => (u && (u.in || u.out)) ? `${kfmt(u.in || 0)}/${kfmt(u.out || 0)} tok` : "";
function runDuration(r) {
  const start = parseTs(r.ts);
  if (start == null) return "";
  const end = TERMINAL.has(r.state) ? (r.updated ? Date.parse(r.updated) : null) : Date.now();
  return end && end >= start ? fmtDur((end - start) / 1000) : "";
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
  let optionsBuilt = false;

  // ---- header --------------------------------------------------------------
  const liveDot = el("span", { class: "dot", title: "daemon link" });
  const refreshBtn = el("button", { class: "btn small" }, "↻ refresh");
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("h1", {}, "Activity log"),
      el("div", { class: "muted", style: "font-size:13px" },
        "every run across every routine — live")),
    el("div", { class: "row" }, refreshBtn,
      el("span", { class: "row", style: "gap:6px" }, liveDot,
        el("span", { class: "muted", style: "font-family:var(--mono);font-size:12px" }, "daemon")))));

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
      el("span", { class: "muted", style: "font-family:var(--mono);font-size:12px" }, "live-follow"))));

  const feed = el("div", { class: "feed" });
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
      if (!optionsBuilt) buildRoutineOptions(routines);
      renderStats();
      renderFeed();
    } catch (err) {
      liveDot.classList.remove("on");                 // stay quiet on transient poll failures
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
      statCard(runningNow, "running now", "phos", () => setStatusFilter("running")),
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
    feed.replaceChildren(...(els.length ? els
      : [el("div", { class: "empty" }, "No runs match these filters.")]));
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

    let expanded = false, source = null, transcript = null, built = false, cur = r0;

    function update(r) {
      cur = r;
      if (stateChip.textContent !== r.state) { stateChip.textContent = r.state; stateChip.className = `chip ${r.state}`; }
      nameEl.textContent = routineMeta[r.routine]?.name || r.routine;
      const bits = [fmtTs(r.ts), `${r.turn || 0} turns`, compactTokens(r.usage), runDuration(r)];
      metaEl.textContent = bits.filter(Boolean).join("  ·  ");
      const oneLine = (r.summary || "").split("\n").find((l) => l.trim())
        || (isActive(r.state) ? "…in progress" : "(no summary)");
      sumEl.textContent = oneLine;
      sumEl.title = r.summary || "";
    }

    async function build() {
      built = true;
      transcript = createTranscript(body);
      if (isActive(cur.state)) {
        source = sse(`/api/runs/${r0.run_id}/events`, {
          transcript: (ev) => transcript && transcript.add(ev),
          state: () => {},                                   // header refreshes via load()
          end: () => { if (source) { source.close(); source = null; } },
          onerror: () => {},
        });
      } else {
        try {
          const { events } = await api(`/api/runs/${r0.run_id}/transcript`);
          if (!transcript) return;                           // collapsed while fetching
          if (!events.length) body.append(el("div", { class: "empty" }, "empty transcript"));
          for (const ev of events) transcript.add(ev);
        } catch (err) {
          body.append(el("div", { class: "ev error" }, `couldn't load transcript: ${err.message}`));
        }
      }
    }

    function closeSource() { if (source) { source.close(); source = null; } }
    function doExpand() {
      if (expanded) return;
      expanded = true; rowEl.classList.add("open"); body.hidden = false;
      build();
    }
    function collapse() {
      expanded = false; built = false; transcript = null;
      rowEl.classList.remove("open"); body.hidden = true; body.innerHTML = "";
      closeSource();
    }
    head.onclick = () => {
      if (expanded) { collapse(); expandedIds.delete(r0.run_id); }
      else { doExpand(); expandedIds.add(r0.run_id); }
      syncURL();
    };

    update(r0);
    return { el: rowEl, update, dispose: closeSource, ensureOpen: doExpand };
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
