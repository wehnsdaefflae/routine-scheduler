// The shared browser's screen: the noVNC page the container's websockify serves, embedded
// full-height so the browser the routines drive can be WATCHED — and, here, driven.
//
// The operator asked for this after opening the port himself: "i opened the tailgate port
// and it seems like i got access... can't we make the novnc window into the browser
// available as a link in the left major sidebar". The address is `browser_view_url` in the
// instance config, because whether that port is reachable at all is a fact about the
// deployment's networking, not something the process can derive.
//
// This page is the INTERACTIVE one (the right-rail dock is the read-only mirror): signing a
// session in is the reason the screen exists, and that needs a keyboard.

import { api } from "/static/api.js";
import { el, emptyState, skeleton } from "/static/util.js";

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
  const frame = el("iframe", { src: url, class: "browser-screen",
    // the point of this page is to TYPE into the session (signing in), so it is not sandboxed
    // down to a picture — the read-only mirror in the rail is the one that is
    allow: "clipboard-read; clipboard-write" });
  box.replaceChildren(
    el("div", { class: "row", style: "gap:8px;align-items:center" },
      el("span", { class: "faint small" }, url),
      el("a", { class: "btn small", href: url, target: "_blank", rel: "noreferrer" },
        "open in a tab")),
    el("div", { class: "mt browser-screen-wrap" }, frame));
}
