// The desktop preview dock: a READ-ONLY corner window onto the agent desktop that worked most
// recently, beside the browser dock in the bottom-right column — so a routine clicking through
// a desktop program can be watched from any page, the way the shared browser can.
//
// What every dock does is components/screendock.js (read-only frame, resting open only at
// ≥1900px, dialling only once open and on screen, the "screen unreachable" note, hiding on its
// own page). What is the desktops' own is here: there may be none, one or several screens, and
// they come and go — a desktop boots on its routine's first desktop command and stops when idle.
// So the dock is HIDDEN while nothing runs, previews the most recently active desktop when it
// first finds one, keeps that one while it lives (re-dialling at every poll whenever another
// became busier would reconnect the frame every few seconds), and carries a small switcher once
// there are several. The list is the console's one reader of /api/desktops (desktops-store.js),
// shared with the Desktops page.

import { api } from "/static/api.js";
import { el } from "/static/util.js";
import { mountScreenDock } from "/static/components/screendock.js";
import { DESKTOP, grantPass } from "/static/components/screen.js";
import { labelOf, loadDesktops, subscribeDesktops } from "/static/desktops-store.js";

export async function initDesktopDock() {
  const slot = document.getElementById("desktop-dock");
  if (!slot) return;

  let configured = false;
  try { configured = Boolean((await api("/api/status")).desktops); }
  catch { return; }               // the daemon lamp already reports connectivity
  // Not configured: no dock and no nav link — an instance without the desktop sidecar should
  // not show a door to it.
  if (!configured) return;

  const navLink = document.getElementById("nav-desktops");
  if (navLink) navLink.hidden = false;

  // One pass opens every desktop's screen: the relay is one path, and the socket's token is
  // what picks the screen (F530 for why it is a cookie at all).
  try { await grantPass(DESKTOP); }
  catch { return; }

  const dock = mountScreenDock(slot, { prefix: DESKTOP, label: "Desktop", page: "#/desktops",
    storageKey: "desktop-dock-open", frameTitle: "agent desktop (read-only)" });
  dock.setPresent(false);

  let shown = "";                 // the desktop NAME the dock shows; kept while it is running
  const pick = el("select", { class: "sd-switch", "aria-label": "desktop to preview",
    title: "which desktop to preview" });
  pick.addEventListener("change", () => { shown = pick.value; paint(latest); });

  const which = el("span", { class: "sd-which" });

  let latest = null;
  function paint(fleet) {
    // a read that failed says nothing about the desktops: keep what is shown rather than hang
    // up on a screen that is very likely still there
    if (fleet?.error) return;
    latest = fleet;
    // a desktop still booting has no screen yet, and one with no usable token never will
    const live = (fleet?.desktops || []).filter((d) => d.ready && d.vnc);
    dock.setPresent(live.length > 0);
    if (!live.length) { shown = ""; dock.show(null); dock.setSwitcher(null); return; }
    // the API lists the most recently active first
    const current = live.find((d) => d.name === shown) || live[0];
    shown = current.name;
    if (live.length > 1) {
      // rebuilt only when the set changes: replacing the options under an open <select> at
      // every poll would snap it shut in the operator's hand
      // (listed by name, not by activity, so the order does not shuffle as idle times move)
      const byName = [...live].sort((a, b) => a.name.localeCompare(b.name));
      const names = byName.map((d) => d.name).join("\n");
      if (pick.dataset.names !== names) {
        pick.replaceChildren(...byName.map((d) => el("option", { value: d.name }, labelOf(d))));
        pick.dataset.names = names;
      }
      pick.value = current.name;
      dock.setSwitcher(pick);
    } else {
      which.textContent = labelOf(current);
      dock.setSwitcher(which);
    }
    dock.show({ key: `${current.name}:${current.vnc}`, token: current.vnc });
  }

  subscribeDesktops(paint);       // the dock lives as long as the console: never unsubscribed
  loadDesktops();
}
