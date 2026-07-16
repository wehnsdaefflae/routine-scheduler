// Entry: hash router (path + query), location indicators (active nav + breadcrumb), the
// in-flight setup banner, the first-launch self-improvement notice, the topbar clock, and
// the global SSE stream (badges + daemon lamp).

import { api, sse } from "/static/api.js";
import { parseHash } from "/static/router.js";
import { installTracing } from "/static/trace.js";
import { installFormPersistence } from "/static/formpersist.js";
import { el, fmtTs, skeleton, startTimeTicker, storage, toast } from "/static/util.js";
import { initNotifications } from "/static/notify.js";
import { initTaskManager } from "/static/components/taskmanager.js";

installTracing();
installFormPersistence();

const routes = [
  [/^#?\/?$/, () => import("/static/views/dashboard.js")],
  [/^#\/conversations(?:\/([a-z0-9-]+))?$/, () => import("/static/views/conversations.js")],
  [/^#\/log$/, () => import("/static/views/log.js")],
  [/^#\/audit$/, () => import("/static/views/audit.js")],
  [/^#\/stats$/, () => import("/static/views/stats.js")],
  [/^#\/routine\/([a-z0-9-]+)$/, () => import("/static/views/routine.js")],
  [/^#\/run\/([a-z0-9-]+:[0-9-]+)$/, () => import("/static/views/run.js")],
  [/^#\/questions$/, () => import("/static/views/questions.js")],
  [/^#\/library(?:\/(.*))?$/, () => import("/static/views/library.js")],
  [/^#\/new-routine$/, () => import("/static/views/new-routine.js")],
  [/^#\/settings$/, () => import("/static/views/settings.js")],
  [/^#\/help(?:\/(.*))?$/, () => import("/static/views/help.js")],
];

let teardown = null;
let navToken = 0;   // bumped per navigation; lets a superseded route() detect it lost the race

async function route() {
  const { path, query } = parseHash();
  for (const [pattern, load] of routes) {
    const m = pattern.exec(path);
    if (!m) continue;
    const token = ++navToken;
    const view = document.getElementById("view");
    if (teardown) { try { teardown(); } catch { /* view already gone */ } teardown = null; }
    // Each navigation renders into its OWN container. A view keeps appending to the element it
    // was handed across its awaits — quick tab switches used to leave a stale render writing
    // into the live tab. Detached container = stale writes land nowhere visible.
    const box = el("div", {});
    // Instant skeleton while the module loads; each view then swaps in its own skeleton
    // synchronously before fetching data — no view ever paints blank.
    box.append(skeleton(["30%", "100%", "100%", "70%", "45%"]));
    view.replaceChildren(box);
    updateLocation(path);
    try {
      const mod = await load();
      if (token !== navToken) return;   // superseded while the module loaded
      box.replaceChildren();
      // Views receive their path params spread, then the parsed query object last.
      const td = (await mod.render(box, ...m.slice(1), query)) || null;
      // Superseded mid-render: the container is detached — release the view's listeners now.
      if (token !== navToken) { try { td?.(); } catch { /* already gone */ } return; }
      teardown = td;
    } catch (err) {
      if (token !== navToken) return;
      box.replaceChildren(el("div", { class: "empty" },
        el("div", { class: "t" }, "view failed to load"),
        el("div", { class: "d" }, err.message)));
    }
    return;
  }
  location.hash = "#/";
}

// ---- location indicators: active nav + breadcrumb -------------------------------------------
function updateLocation(path) {
  const key = path.startsWith("#/log") ? "log"
    : path.startsWith("#/conversations") ? "conversations"
    : path.startsWith("#/questions") ? "questions"
    : path.startsWith("#/audit") ? "audit"
    : path.startsWith("#/stats") ? "stats"
    : path.startsWith("#/library") ? "library"
    : path.startsWith("#/settings") ? "settings"
    : path.startsWith("#/help") ? "help"
    : path.startsWith("#/new-routine") ? "new-routine"
    : "dashboard";
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === key));
  renderCrumbs(path);
}

// Breadcrumb built from the URL alone (no extra fetches) — earlier segments link back up.
function crumbsFor(path) {
  const parts = path.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const top = parts[0] || "";
  switch (top) {
    case "": return [{ label: "Routines" }];
    case "log": return [{ label: "Log" }];
    case "questions": return [{ label: "Decisions" }];
    case "audit": return [{ label: "Audit" }];
    case "stats": return [{ label: "Stats" }];
    case "settings": return [{ label: "Settings" }];
    case "help": {
      const c = [{ label: "Help", href: parts.length > 1 ? "#/help" : null }];
      if (parts[1]) c.push({ label: parts[1] });
      return c;
    }
    case "library": {
      const c = [{ label: "Library", href: parts.length > 1 ? "#/library" : null }];
      if (parts[1]) c.push({ label: parts[1] });
      if (parts[2]) c.push({ label: parts[2] });
      return c;
    }
    case "conversations": {
      const c = [{ label: "Conversations", href: parts.length > 1 ? "#/conversations" : null }];
      if (parts[1]) c.push({ label: parts[1] });
      return c;
    }
    case "routine": return [{ label: "Routines", href: "#/" }, { label: parts[1] || "" }];
    case "run": {
      const [slug, ts] = (parts[1] || "").split(":");
      return [{ label: "Routines", href: "#/" },
        { label: slug || "run", href: slug ? `#/routine/${slug}` : null },
        { label: ts ? `run ${fmtTs(ts)}` : "run" }];
    }
    case "new-routine": return [{ label: "Routines", href: "#/" }, { label: "New routine" }];
    default: return [{ label: "Routines" }];
  }
}

function renderCrumbs(path) {
  const bar = document.getElementById("crumbs");
  if (!bar) return;
  const segs = crumbsFor(path);
  bar.replaceChildren();
  segs.forEach((s, i) => {
    if (i) bar.append(el("span", { class: "sep" }, "›"));
    bar.append(s.href && i < segs.length - 1
      ? el("a", { href: s.href }, s.label)
      : el("span", { class: i === segs.length - 1 ? "here" : "" }, s.label));
  });
}

// ---- in-flight setup banner ------------------------------------------------------------------
// While any new-routine setup session is live, show a persistent, always-visible way back to it
// on every view — the clarify run's page, where the setup panel lives (D11). Driven by
// /api/wizard so it is correct across reloads, tabs, and daemon restarts.
const STAGE_LABEL = { chat: "clarifying", suggest: "choosing a workflow",
                      building: "building the routine", error: "needs attention" };

async function refreshSetupBanner() {
  const banner = document.getElementById("setup-banner");
  let sessions = [];
  try { sessions = await api("/api/wizard"); } catch { return; }   // stay quiet on transient errors
  const active = Array.isArray(sessions) ? sessions : [];
  const cur = active[0];   // newest first
  if (!cur) { banner.hidden = true; banner.replaceChildren(); return; }
  const more = active.length > 1 ? ` (+${active.length - 1} more)` : "";
  // name the session (draft preview) — an abandoned session's banner must not read as if it
  // were about a routine the user just finished creating
  const what = cur.draft ? ` · “${cur.draft.length > 60 ? `${cur.draft.slice(0, 60)}…` : cur.draft}”` : "";
  banner.replaceChildren(
    el("span", { class: "sb-dot" }),
    el("span", { class: "sb-text" },
      el("b", {}, "Routine setup in progress"),
      ` — ${STAGE_LABEL[cur.stage] || "working"}${what}${more}. The backend is still running; pick up where you left off.`),
    // a pre-D13 session has no run page — the new-routine view offers what's left (cancel)
    el("a", { class: "btn small primary",
      href: cur.clarify_run_id ? `#/run/${cur.clarify_run_id}` : "#/new-routine" }, "resume"));
  banner.hidden = false;
}

// ---- first-launch notice: self-improvement routines are off ---------------------------------
// The bundled meta routines install disabled (no hidden costs). Until at least one is enabled
// the system never improves itself — surface that once, dismissible, with one-click enables.
const META_DISMISS_KEY = "rsched_meta_notice_dismissed";

function renderMetaBanner(metaRoutines) {
  const banner = document.getElementById("meta-banner");
  const all = Array.isArray(metaRoutines) ? metaRoutines : [];
  const show = all.length > 0 && all.every((m) => !m.enabled)
    && !storage.get(META_DISMISS_KEY);
  if (!show) { banner.hidden = true; banner.replaceChildren(); return; }
  const enableBtn = (m) => {
    const b = el("button", { class: "btn small" }, `enable ${m.slug}`);
    b.onclick = async () => {
      b.disabled = true;
      try {
        await api(`/api/routines/${m.slug}`, { method: "PATCH", body: { enabled: true } });
        toast(`${m.slug} enabled — it now runs on its schedule`);
        b.replaceWith(el("span", { class: "chip ok" }, "enabled"));
        refreshStatus();
      } catch (err) { toast(err.message, 5000, { error: true }); b.disabled = false; }
    };
    return b;
  };
  banner.replaceChildren(
    el("span", { class: "nb-text" },
      el("b", {}, "Self-improvement is off. "),
      "The bundled meta routines ship disabled so a fresh install never spends tokens on its own — ",
      "but the system won't audit or improve itself until you enable them."),
    ...all.map(enableBtn),
    el("button", { class: "nb-close", title: "dismiss (stays dismissed on this browser)",
      onclick: () => { storage.set(META_DISMISS_KEY, "1"); banner.hidden = true; } }, "×"));
  banner.hidden = false;
}

// The tiny build tag next to the brand: release version, with the running checkout's
// commit stamp in the tooltip — enough to identify a deploy at a glance.
function renderVersion(s) {
  const node = document.getElementById("app-version");
  if (!node || !s.version) return;
  node.textContent = `v${s.version}`;
  node.title = s.build ? `v${s.version} · ${s.build}` : `v${s.version}`;
}

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    gateNav(s.llm_ready !== false);
    renderMetaBanner(s.meta_routines);
    renderVersion(s);
    document.getElementById("daemon-dot").classList.add("on");
  } catch {
    document.getElementById("daemon-dot").classList.remove("on");
  }
}

async function refreshBadges() {
  try {
    const qs = await api("/api/questions");
    // answered-but-unconsumed items are settled; snoozed ones wait silently by design
    const open = qs.filter((q) => !q.answered && !q.snoozed);
    const badge = document.getElementById("q-badge");
    badge.textContent = open.length;
    badge.hidden = open.length === 0;
  } catch { /* daemon lamp covers connectivity */ }
}

function globalStream() {
  sse("/api/events", {
    bus: (ev) => {
      if (ev.event === "run_started") toast(`run started: ${ev.run_id}`);
      if (ev.event === "run_finished") toast(`run ${ev.state}: ${ev.run_id}`);
      if (ev.event === "routine_created") { toast(`routine ${ev.slug} is ready`, 5000); refreshSetupBanner(); }
      if (ev.event === "routine_failed") { toast(`routine ${ev.slug} build failed`, 7000, { error: true }); refreshSetupBanner(); }
      if (ev.event === "library_sync" && ev.status !== "ok") toast(`library sync ${ev.status}`, 7000, { error: true });
      refreshBadges();
      window.dispatchEvent(new CustomEvent("rsched-bus", { detail: ev }));
    },
    onopen: () => document.getElementById("daemon-dot").classList.add("on"),
    onerror: () => document.getElementById("daemon-dot").classList.remove("on"),
  });
}

// ---- topbar clock ----------------------------------------------------------------------------
function startClock() {
  const node = document.getElementById("clock");
  const p2 = (n) => String(n).padStart(2, "0");
  const tick = () => {
    const d = new Date();
    node.textContent = `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
  };
  tick();
  setInterval(tick, 1000);
  api("/api/status").then((s) => { if (s.server_tz) node.title = `server tz: ${s.server_tz}`; }).catch(() => {});
}

window.addEventListener("hashchange", route);
// The new-routine view and the run page's setup panel fire this when a session starts / is
// canceled / finalized, so the banner updates immediately instead of waiting for the next poll.
window.addEventListener("rsched-wizard-changed", () => refreshSetupBanner());

function gateNav(ready) {
  const a = document.getElementById("nav-new-routine");   // dim (but keep clickable → gated view)
  if (a) { a.style.opacity = ready ? "" : "0.55"; a.title = ready ? "" : "connect an LLM endpoint in Settings first"; }
}

(async function boot() {
  startClock();
  initNotifications();
  initTaskManager();
  startTimeTicker();
  try {
    const s = await api("/api/status");
    gateNav(s.llm_ready !== false);
    renderMetaBanner(s.meta_routines);
    renderVersion(s);
    // First launch: send the user to setup (Settings) until they finish it. The redirect fires a
    // hashchange → route(), so we don't call route() again in that branch.
    if (s.needs_setup && !location.hash.startsWith("#/settings")) {
      toast("Welcome! Finish setup: add a model provider, connect GitHub, and point at your repos", 6000);
      location.hash = "#/settings";
      return;
    }
  } catch { /* the view will surface the failure */ }
  route();
})();
refreshBadges();
refreshSetupBanner();
globalStream();
setInterval(() => { refreshBadges(); refreshSetupBanner(); refreshStatus(); }, 30000);
