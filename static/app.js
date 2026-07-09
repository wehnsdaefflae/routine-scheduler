// Entry: hash router, nav state, global SSE (badges + daemon dot).

import { api, sse } from "/static/api.js";
import { toast } from "/static/util.js";

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

// Routine-setup lock: while a wizard session is live, the rest of the console is off-limits —
// the process must be finished or canceled first. The wizard view emits rsched-wizard-active.
const WIZ_KEY = "rsched_wizard";
let wizardLocked = false;
const wizardActive = () => {
  try { return !!JSON.parse(localStorage.getItem(WIZ_KEY) || "null"); } catch { return false; }
};

function lockNav(active) {
  wizardLocked = active;
  document.querySelectorAll(".topbar nav a[data-nav]").forEach((a) => a.classList.toggle("locked", active));
  document.querySelector(".brand")?.classList.toggle("locked", active);
  const nb = document.getElementById("nav-new-routine");     // stays live — it's the way back
  if (nb) { nb.classList.toggle("resuming", active); nb.textContent = active ? "↩ Resume setup" : "+ New routine"; }
}

async function route() {
  const hash = location.hash || "#/";
  if (wizardLocked && !hash.startsWith("#/wizard")) {
    toast("Finish or cancel the routine setup first", 3200);
    location.hash = "#/wizard";               // bounce back to the in-progress wizard
    return;
  }
  for (const [pattern, load] of routes) {
    const m = pattern.exec(hash);
    if (!m) continue;
    const view = document.getElementById("view");
    if (teardown) { try { teardown(); } catch {} teardown = null; }
    view.innerHTML = "";
    try {
      const mod = await load();
      teardown = (await mod.render(view, ...m.slice(1))) || null;
    } catch (err) {
      view.innerHTML = `<div class="empty">view failed to load: ${err.message}</div>`;
    }
    updateNav(hash);
    return;
  }
  location.hash = "#/";
}

function updateNav(hash) {
  const key = hash.startsWith("#/log") ? "log"
    : hash.startsWith("#/questions") ? "questions"
    : hash.startsWith("#/audit") ? "audit"
    : hash.startsWith("#/library") ? "library"
    : hash.startsWith("#/settings") ? "settings" : "dashboard";
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === key));
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
window.addEventListener("rsched-wizard-active", (e) => lockNav(!!e.detail?.active));

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
  lockNav(wizardActive());        // restore the setup-lock after a reload mid-wizard
  route();
})();
refreshBadges();
globalStream();
setInterval(refreshBadges, 30000);
