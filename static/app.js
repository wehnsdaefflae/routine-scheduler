// Entry: hash router (path + query), location indicators (active nav + breadcrumb), the
// in-flight setup banner, the first-launch self-improvement notice, the rail's clock and theme
// control, the watch ribbon, and the global SSE stream (badges + daemon lamp).

import { api, sse } from "/static/api.js";
import { parseHash } from "/static/router.js";
import { installTracing } from "/static/trace.js";
import { installFormPersistence } from "/static/formpersist.js";
import { el, fmtTs, skeleton, startTimeTicker, storage, toast } from "/static/util.js";
import { initNotifications } from "/static/notify.js";
import { initTaskManager } from "/static/components/taskmanager.js";
import { initSearchBox } from "/static/components/searchbox.js";
import { mountToc } from "/static/components/toc.js";
import { mountRibbon } from "/static/components/ribbon.js";

installTracing();
installFormPersistence();

const routes = [
  // Conversations is the landing page: the empty hash AND #/conversations both render it
  // (group 1 = the optional slug, undefined at root). The Routines dashboard moved to
  // #/routines. One pattern keeps the render arg shape (box, slug, query) correct at root.
  [/^(?:#?\/?|#\/conversations(?:\/([a-z0-9-]+))?)$/, () => import("/static/views/conversations.js")],
  [/^#\/routines$/, () => import("/static/views/dashboard.js")],
  [/^#\/messages$/, () => import("/static/views/messages.js")],
  [/^#\/stats$/, () => import("/static/views/stats.js")],
  [/^#\/routine\/([a-z0-9-]+)$/, () => import("/static/views/routine.js")],
  [/^#\/run\/([a-z0-9-]+:[0-9-]+)$/, () => import("/static/views/run.js")],
  [/^#\/questions$/, () => import("/static/views/questions.js")],
  [/^#\/summary$/, () => import("/static/views/summary.js")],
  [/^#\/library(?:\/(.*))?$/, () => import("/static/views/library.js")],
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
      // A sticky side TOC of the view's sections (best-effort; no-op on short/rail'd views).
      let tocCleanup = null;
      try { tocCleanup = mountToc(box); } catch { /* toc is a nicety, never fatal */ }
      teardown = () => {
        try { td?.(); } catch { /* view already gone */ }
        try { tocCleanup?.(); } catch { /* toc already gone */ }
      };
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
  // Routines moved to #/routines; #/ (and #/conversations) is the Conversations landing.
  const key = path.startsWith("#/routines") || path.startsWith("#/routine/")
      || path.startsWith("#/run/") ? "dashboard"
    : path.startsWith("#/summary") ? "summary"
    : path.startsWith("#/questions") ? "questions"
    : path.startsWith("#/messages") ? "messages"
    : path.startsWith("#/stats") ? "stats"
    : path.startsWith("#/library") ? "library"
    : path.startsWith("#/settings") ? "settings"
    : path.startsWith("#/help") ? "help"
    : "conversations";
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === key));
  renderCrumbs(path);
}

// Breadcrumb built from the URL alone (no extra fetches) — earlier segments link back up.
function crumbsFor(path) {
  const parts = path.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const top = parts[0] || "";
  switch (top) {
    case "": return [{ label: "Conversations" }];
    case "routines": return [{ label: "Routines" }];
    case "summary": return [{ label: "Summary" }];
    case "questions": return [{ label: "Decisions" }];
    case "messages": return [{ label: "Messages" }];
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
    case "routine": return [{ label: "Routines", href: "#/routines" }, { label: parts[1] || "" }];
    case "run": {
      const [slug, ts] = (parts[1] || "").split(":");
      return [{ label: "Routines", href: "#/routines" },
        { label: slug || "run", href: slug ? `#/routine/${slug}` : null },
        { label: ts ? `run ${fmtTs(ts)}` : "run" }];
    }
    default: return [{ label: "Conversations" }];
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

// ---- first-launch notice: self-improvement routines are off ---------------------------------
// A meta routine is created disabled (no hidden costs). Until at least one is enabled the
// system never improves itself — surface that once, dismissible, with one-click enables.
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
      "Meta routines start disabled so an instance never spends tokens on its own — ",
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

// ONE status path: the boot render and the 30s poll are the same call, and it returns the
// payload so boot can read `needs_setup` without a second fetch. They used to be two copies of
// the same three lines, and that duplication is why a dead `gateNav(...)` call at the head of
// the poll went unnoticed: it threw a ReferenceError on every tick, so the catch below ran
// instead and the daemon lamp was switched OFF thirty seconds after every load and stayed off —
// a console permanently claiming the daemon was down, while the boot copy kept working. Nothing
// caught it because the poll was reached only on a timer no test outlives; sharing the path puts
// it under every UI test's js_errors collector. (Navigation gating moved into the views
// themselves — dashboard.js / routine.js read `llm_ready` and disable their own run controls.)
async function refreshStatus() {
  const clock = document.getElementById("clock");
  try {
    const s = await api("/api/status");
    renderMetaBanner(s.meta_routines);
    renderVersion(s);
    if (clock && s.server_tz) clock.title = `server tz: ${s.server_tz}`;
    document.getElementById("daemon-dot").classList.add("on");
    return s;
  } catch {
    document.getElementById("daemon-dot").classList.remove("on");
    return null;
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

// Coalesce badge refreshes: the bus can storm (llm_task lifecycle events fire sub-second
// during a busy run) and every event used to cost a GET /api/questions. llm_task events
// can't change decisions at all; everything else refreshes at most once per window,
// with one trailing refresh so the final state always lands.
const BADGE_REFRESH_MIN_MS = 3000;
let badgeCooldown = 0, badgeTrailing = false;
function scheduleBadgeRefresh() {
  if (badgeCooldown) { badgeTrailing = true; return; }
  refreshBadges();
  badgeCooldown = setTimeout(() => {
    badgeCooldown = 0;
    if (badgeTrailing) { badgeTrailing = false; scheduleBadgeRefresh(); }
  }, BADGE_REFRESH_MIN_MS);
}

function globalStream() {
  // The global event stream drives every view's live refresh (dashboard routine states,
  // decision badges, run toasts). EventSource's OWN auto-reconnect reuses the same
  // ?ticket= URL, but SSE tickets have a 60s TTL and are purged whenever the daemon
  // restarts — so a naive reconnect 401s forever, the bus goes silent, and the console
  // freezes with stale routine states (the daemon lamp stuck off). So we own the
  // reconnect the way stream.js/liveTail does: on error, close the dead source and reopen
  // via a fresh sse() (which mints a NEW ticket) under capped exponential backoff.
  let source = null, timer = null, retry = 0;
  const dot = () => document.getElementById("daemon-dot");
  const handlers = {
    bus: (ev) => {
      retry = 0;   // a delivered event proves the stream is healthy — reset the backoff
      if (ev.event === "run_started") toast(`run started: ${ev.run_id}`);
      if (ev.event === "run_finished") toast(`run ${ev.state}: ${ev.run_id}`);
      if (ev.event === "routine_created") toast(`routine ${ev.slug} is ready`, 5000);
      if (ev.event === "routine_failed") toast(`routine ${ev.slug} build failed`, 7000, { error: true });
      if (ev.event !== "llm_task") scheduleBadgeRefresh();
      window.dispatchEvent(new CustomEvent("rsched-bus", { detail: ev }));
    },
    onopen: () => {
      retry = 0;
      dot()?.classList.add("on");
      // A reconnect may have missed run start/state/finish events while the stream was down;
      // fire one synthetic bus tick so every view re-fetches from REST and catches up (on the
      // first open the views have just loaded, so this is a cheap no-op refresh).
      window.dispatchEvent(new CustomEvent("rsched-bus", { detail: { event: "reconnect" } }));
    },
    onerror: () => {
      dot()?.classList.remove("on");
      if (source) { source.close(); source = null; }   // stop EventSource retrying the dead ticket
      clearTimeout(timer);
      const delay = Math.min(15000, 1000 * 2 ** retry);   // capped exponential backoff
      retry += 1;
      timer = setTimeout(connect, delay);
    },
  };
  function connect() { source = sse("/api/events", handlers); }
  connect();
}

// ---- the rail's clock ------------------------------------------------------------------------
function startClock() {
  const node = document.getElementById("clock");
  const p2 = (n) => String(n).padStart(2, "0");
  const tick = () => {
    const d = new Date();
    node.textContent = `${p2(d.getHours())}:${p2(d.getMinutes())}`;
  };
  tick();
  setInterval(tick, 1000);
}

// ---- theme: auto / light / dark ---------------------------------------------------------------
// Dark is the shipped default, so a console nobody has touched looks exactly as it did. `auto`
// is an explicit CHOICE to follow the machine, not the absence of one — which is why the stored
// value is written for all three and the inline script in index.html applies it before paint.
const THEME_KEY = "rsched_theme";
const THEMES = [["auto", "follow this device's light or dark setting"],
                ["light", "always light"], ["dark", "always dark"]];

function initTheme() {
  const host = document.getElementById("theme-toggle");
  if (!host) return;
  const current = () => document.documentElement.dataset.theme || "dark";
  const paint = () => {
    host.replaceChildren(...THEMES.map(([name, why]) => {
      const b = el("button", { type: "button", title: why,
                               "aria-pressed": String(current() === name) }, name);
      b.onclick = () => {
        document.documentElement.dataset.theme = name;
        storage.set(THEME_KEY, name);
        paint();
      };
      return b;
    }));
  };
  paint();
}

window.addEventListener("hashchange", route);

(async function boot() {
  startClock();
  initTheme();
  const ribbonHost = document.getElementById("ribbon");
  if (ribbonHost) mountRibbon(ribbonHost);
  initNotifications();
  initTaskManager();
  initSearchBox();
  startTimeTicker();
  const s = await refreshStatus();   // renders version, meta banner, lamp and clock tooltip
  // First launch: send the user to setup (Settings) until they finish it. The redirect fires a
  // hashchange → route(), so we don't call route() again in that branch.
  if (s?.needs_setup && !location.hash.startsWith("#/settings")) {
    toast("Welcome! Finish setup: add a model provider, connect GitHub, and point at your repos", 6000);
    location.hash = "#/settings";
    return;
  }
  route();
})();
refreshBadges();
globalStream();
setInterval(() => { refreshBadges(); refreshStatus(); }, 30000);
