// The browser preview dock: a permanent, READ-ONLY window onto the shared signed-in browser,
// mounted in the right rail on every page.
//
// The operator's ask, alongside the nav link: "provide a permanent read only preview in the
// right minor sidebar". Permanent is the point — the routines drive that browser unattended,
// and a screen you have to navigate to is a screen you never look at.
//
// READ-ONLY IS ENFORCED, not implied: the iframe carries `pointer-events: none` (base.css) and
// is inert to the keyboard, so a passing click cannot steer a live session mid-run. The
// interactive screen is one click away at #/browser, which is where typing belongs.
//
// It mounts into #browser-dock, a sibling of .workspace (like #llm-tasks), so it survives view
// navigation — the iframe is never re-created by routing, and the VNC connection therefore
// stays up instead of reconnecting on every page change.

import { api } from "/static/api.js";
import { el, storage } from "/static/util.js";
// ONE builder for the relayed noVNC URL, shared with the full screen: two copies of that
// query shape would drift, and the `path` parameter is the load-bearing part.
import { frameSrc } from "/static/views/browser.js";

const KEY_OPEN = "browser-dock-open";

export async function initBrowserDock() {
  const slot = document.getElementById("browser-dock");
  if (!slot) return;

  let url = "";
  try { url = (await api("/api/status")).browser_view_url || ""; }
  catch { return; }               // the daemon lamp already reports connectivity
  // Nothing published: no dock, no nav link, no explanation needed here — Settings says it.
  if (!url) return;

  const navLink = document.getElementById("nav-browser");
  if (navLink) navLink.hidden = false;

  let open = storage.get(KEY_OPEN) !== "0";   // shown by default once a screen exists
  slot.hidden = false;

  // Relayed through the console's own origin, exactly like the full screen (F527): the raw
  // upstream is http, and an https console may not open the ws:// socket noVNC needs, so a
  // direct embed showed a permanently blank preview. The ticket is for that socket.
  let ticket = "";
  try { ticket = (await api("/api/sse-ticket", { method: "POST", body: {} })).ticket || ""; }
  catch { /* auth disabled, or the ticket route refused — the relay then needs none */ }
  const frame = el("iframe", { class: "browser-dock-frame", title: "shared browser (read-only)",
    // view_only is noVNC's own switch; the CSS makes it inert regardless of what the page does
    src: frameSrc(ticket, { viewOnly: true }),
    tabindex: "-1", "aria-hidden": "true" });
  const title = el("a", { class: "bd-title", href: "#/browser",
    title: "open the full screen — that one takes the keyboard" }, "Browser");
  const toggle = el("button", { class: "bd-toggle", type: "button" });
  const head = el("div", { class: "bd-head" }, title, toggle);
  const body = el("div", { class: "bd-body" }, frame,
    el("div", { class: "bd-note faint small" }, "read-only · click the title to take over"));

  const paint = () => {
    slot.classList.toggle("bd-collapsed", !open);
    body.hidden = !open;
    toggle.textContent = open ? "hide" : "show";
    toggle.title = open ? "collapse the preview" : "show the browser preview";
  };
  toggle.onclick = () => { open = !open; storage.set(KEY_OPEN, open ? "1" : "0"); paint(); };
  slot.replaceChildren(head, body);
  paint();
}
