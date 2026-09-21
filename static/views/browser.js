// The shared browser's screen: the noVNC page the container's websockify serves, embedded
// full-height so the browser the routines drive can be WATCHED — and, here, driven.
//
// The operator asked for this after opening the port himself: "i opened the tailgate port
// and it seems like i got access... can't we make the novnc window into the browser
// available as a link in the left major sidebar".
//
// IT LOADS THROUGH THE CONSOLE'S OWN ORIGIN (`/browser-view/…`), never from the configured
// address directly. 0.359.0 embedded the raw `browser_view_url` and the screen was blank over
// https: the frame itself loaded, but noVNC then opened `ws://…/websockify`, and a page served
// over https may not open an insecure websocket — so no framebuffer ever arrived. Same-origin
// means the browser upgrades to `wss://` by itself, under the TLS the console already
// terminates: no certificate on the noVNC port, no second hostname to publish (F527).
//
// This page is the INTERACTIVE one (the right-rail dock is the read-only mirror): signing a
// session in is the reason the screen exists, and that needs a keyboard.

import { api } from "/static/api.js";
import { el, emptyState, skeleton } from "/static/util.js";

// noVNC takes its websocket location from `path` — relative to the page it was loaded from,
// which is now this console. The ticket rides there because the WebSocket API cannot send the
// bearer header (the same reason the event streams use one).
export function frameSrc(ticket, { viewOnly = false } = {}) {
  const path = `browser-view/websockify${ticket ? `?ticket=${encodeURIComponent(ticket)}` : ""}`;
  const q = new URLSearchParams({ autoconnect: "1", resize: "scale", path });
  if (viewOnly) q.set("view_only", "1");
  return `/browser-view/vnc.html?${q}`;
}

export async function render(view) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("h1", {}, "Browser"),
      el("div", { class: "sub" },
        "the shared signed-in browser the routines drive — watch it, or take the keyboard"))));
  const box = el("div", { class: "mt" }, skeleton(["100%", "100%"]));
  view.append(box);

  let status;
  try { status = await api("/api/status"); }
  catch (err) {
    box.replaceChildren(emptyState("✕", "Couldn't reach the daemon", err.message));
    return;
  }
  const url = status.browser_view_url || "";
  if (!url) {
    // No URL is a legitimate state, not a failure: most instances never publish the screen.
    // Say what to do about it rather than showing an empty frame.
    box.replaceChildren(emptyState("◌", "No browser screen is published",
      "The shared browser runs headless inside the container. To watch it, serve its noVNC "
      + "page (websockify, usually port 6080) somewhere this console can reach, then set "
      + "that address under Settings → server process → browser screen (noVNC) URL."),
      el("div", { class: "row mt" },
        el("a", { class: "btn small primary", href: "#/settings" }, "open Settings")));
    return;
  }
  let ticket = "";
  try { ticket = (await api("/api/sse-ticket", { method: "POST", body: {} })).ticket || ""; }
  catch { /* auth disabled, or the ticket route refused — the relay then needs none */ }
  const frame = el("iframe", { src: frameSrc(ticket), class: "browser-screen",
    // the point of this page is to TYPE into the session (signing in), so it is not sandboxed
    // down to a picture — the read-only mirror in the rail is the one that is
    allow: "clipboard-read; clipboard-write" });
  box.replaceChildren(
    el("div", { class: "row", style: "gap:8px;align-items:center" },
      el("span", { class: "faint small" }, "relayed through this console · ",
        el("code", { class: "small" }, url)),
      el("a", { class: "btn small", href: url, target: "_blank", rel: "noreferrer" },
        "open the upstream directly")),
    el("div", { class: "mt browser-screen-wrap" }, frame));
}
