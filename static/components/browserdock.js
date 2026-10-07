// The browser preview dock: a permanent, READ-ONLY window onto the shared signed-in browser,
// in the bottom-right column of docks on every page.
//
// The operator's ask, alongside the nav link: "provide a permanent read only preview in the
// right minor sidebar". Permanent is the point — the routines drive that browser unattended,
// and a screen you have to navigate to is a screen you never look at.
//
// Everything a dock DOES — read-only frame, resting open only at ≥1900px, dialling only once it
// is open and on screen, the quiet "screen unreachable" note, hiding on its own page — is the
// shared screen dock (components/screendock.js), which the desktop dock is too. What is the
// browser's own is here: there is exactly one screen, and it exists once `browser_view_url` is
// set.

import { api } from "/static/api.js";
import { mountScreenDock } from "/static/components/screendock.js";
import { BROWSER, grantPass } from "/static/components/screen.js";

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

  // The pass covers the document, noVNC's own asset fetches and the socket handshake alike
  // (F530). Without it every one of those is refused and the frame shows the API's 401 body —
  // which is exactly what the preview did on its first release.
  try { await grantPass(BROWSER); }
  catch { return; }               // no pass, no preview: better absent than showing an error

  const dock = mountScreenDock(slot, { prefix: BROWSER, label: "Browser", page: "#/browser",
    storageKey: "browser-dock-open", frameTitle: "shared browser (read-only)" });
  dock.show({ key: "browser", token: "" });
}
