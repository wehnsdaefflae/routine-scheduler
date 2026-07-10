// Entry: hash router (path + query), location indicators (active nav + breadcrumb),
// the in-flight setup banner, and the global SSE stream (badges + daemon dot).

import { api, sse } from "/static/api.js";
import { parseHash } from "/static/router.js";
import { el, fmtTs, toast } from "/static/util.js";

const routes = [
  [/^#?\/?$/, () => import("/static/views/dashboard.js")],
  [/^#\/log$/, () => import("/static/views/log.js")],
  [/^#\/audit$/, () => import("/static/views/audit.js")],
  [/^#\/routine\/([a-z0-9-]+)$/, () => import("/static/views/routine.js")],
  [/^#\/run\/([a-z0-9-]+:[0-9-]+)$/, () => import("/static/views/run.js")],
  [/^#\/questions$/, () => import("/static/views/questions.js")],
  [/^#\/library(?:\/(.*))?$/, () => import("/static/views/library.js")],
  [/^#\/wizard(?:\/(.+))?$/, () => import("/static/views/wizard.js")],
  [/^#\/settings$/, () => import("/static/views/settings.js")],
];

let teardown = null;

async function route() {
  const { path, query } = parseHash();
  for (const [pattern, load] of routes) {
    const m = pattern.exec(path);
    if (!m) continue;
    const view = document.getElementById("view");
    if (teardown) { try { teardown(); } catch {} teardown = null; }
    view.innerHTML = "";
    try {
      const mod = await load();
      // Views receive their path params spread, then the parsed query object last.
      teardown = (await mod.render(view, ...m.slice(1), query)) || null;
    } catch (err) {
      view.innerHTML = `<div class="empty">view failed to load: ${err.message}</div>`;
    }
    updateLocation(path);
    return;
  }
  location.hash = "#/";
}

// ---- location indicators: active nav + breadcrumb -------------------------------------------
function updateLocation(path) {
  const key = path.startsWith("#/log") ? "log"
    : path.startsWith("#/questions") ? "questions"
    : path.startsWith("#/audit") ? "audit"
    : path.startsWith("#/library") ? "library"
    : path.startsWith("#/settings") ? "settings"
    : path.startsWith("#/wizard") ? "wizard"
    : (path.startsWith("#/routine") || path.startsWith("#/run") || path === "#/" || path === "#") ? "dashboard"
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
    case "": return [{ label: "Dashboard" }];
    case "log": return [{ label: "Log" }];
    case "questions": return [{ label: "Decisions" }];
    case "audit": return [{ label: "Audit" }];
    case "settings": return [{ label: "Settings" }];
    case "library": {
      const c = [{ label: "Library", href: parts.length > 1 ? "#/library" : null }];
      if (parts[1]) c.push({ label: parts[1] });
      if (parts[2]) c.push({ label: parts[2] });
      return c;
    }
    case "routine": return [{ label: "Routines", href: "#/" }, { label: parts[1] || "" }];
    case "run": {
      const [slug, ts] = (parts[1] || "").split(":");
      return [{ label: "Routines", href: "#/" },
        { label: slug || "run", href: slug ? `#/routine/${slug}` : null },
        { label: ts ? `run ${fmtTs(ts)}` : "run" }];
    }
    case "wizard": return [{ label: "Routines", href: "#/" }, { label: "New routine" }];
    default: return [{ label: "Dashboard" }];
  }
}

function renderCrumbs(path) {
  const bar = document.getElementById("crumbs");
  if (!bar) return;
  const segs = crumbsFor(path);
  bar.innerHTML = "";
  segs.forEach((s, i) => {
    if (i) bar.append(el("span", { class: "sep" }, "›"));
    bar.append(s.href && i < segs.length - 1
      ? el("a", { href: s.href }, s.label)
      : el("span", { class: i === segs.length - 1 ? "here" : "" }, s.label));
  });
}

// ---- in-flight setup banner (replaces the old hard nav-lock) --------------------------------
// While any new-routine wizard session is live, show a persistent, always-visible way back to it
// on every view. Driven by /api/wizard so it is correct across reloads, tabs, and daemon restarts.
const STAGE_LABEL = { chat: "clarifying", suggest: "choosing a workflow", error: "needs attention" };

async function refreshSetupBanner() {
  const banner = document.getElementById("setup-banner");
  const newBtn = document.getElementById("nav-new-routine");
  let sessions = [];
  try { sessions = await api("/api/wizard"); } catch { return; }   // stay quiet on transient errors
  const active = Array.isArray(sessions) ? sessions : [];
  const cur = active[0];   // newest first
  if (newBtn) {
    newBtn.classList.toggle("resuming", !!cur);
    newBtn.textContent = cur ? "↩ Resume setup" : "+ New routine";
    newBtn.setAttribute("href", cur ? `#/wizard/${cur.wid}` : "#/wizard");
  }
  if (!cur) { banner.hidden = true; banner.innerHTML = ""; return; }
  const more = active.length > 1 ? ` (+${active.length - 1} more)` : "";
  banner.innerHTML = "";
  banner.append(
    el("span", { class: "sb-dot" }),
    el("span", { class: "sb-text" },
      el("b", {}, "Routine setup in progress"),
      ` — ${STAGE_LABEL[cur.stage] || "working"}${more}. The backend is still running; pick up where you left off.`),
    el("a", { class: "btn small primary", href: `#/wizard/${cur.wid}` }, "resume"));
  banner.hidden = false;
}

async function refreshBadges() {
  try {
    const qs = await api("/api/questions");
    const badge = document.getElementById("q-badge");
    badge.textContent = qs.length;
    badge.hidden = qs.length === 0;
    document.getElementById("daemon-dot").classList.add("on");
  } catch {
    document.getElementById("daemon-dot").classList.remove("on");
  }
}

function globalStream() {
  sse("/api/events", {
    bus: (ev) => {
      if (ev.event === "run_started") toast(`run started: ${ev.run_id}`);
      if (ev.event === "run_finished") toast(`run ${ev.state}: ${ev.run_id}`);
      refreshBadges();
      window.dispatchEvent(new CustomEvent("rsched-bus", { detail: ev }));
    },
    onerror: () => document.getElementById("daemon-dot").classList.remove("on"),
  });
}

window.addEventListener("hashchange", route);
// The wizard view fires this when a session starts / is canceled / finalized, so the banner
// updates immediately instead of waiting for the next poll.
window.addEventListener("rsched-wizard-changed", () => refreshSetupBanner());

// First launch: send the user to setup (Settings) until they finish it. The redirect fires a
// hashchange → route(), so we don't call route() again in that branch.
function gateNav(ready) {
  const a = document.getElementById("nav-new-routine");   // dim (but keep clickable → gated wizard)
  if (a) { a.style.opacity = ready ? "" : "0.55"; a.title = ready ? "" : "connect an LLM endpoint in Settings first"; }
}

(async function boot() {
  try {
    const s = await api("/api/status");
    gateNav(s.llm_ready !== false);
    if (s.needs_setup && !location.hash.startsWith("#/settings")) {
      toast("Welcome! Finish setup: add a model provider, connect GitHub, and point at your repos", 6000);
      location.hash = "#/settings";
      return;
    }
  } catch {}
  route();
})();
refreshBadges();
refreshSetupBanner();
globalStream();
setInterval(() => { refreshBadges(); refreshSetupBanner(); }, 30000);
