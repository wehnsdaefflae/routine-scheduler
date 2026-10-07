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
// terminates: no certificate on the noVNC port, no second hostname to publish (F527). The URL,
// the pass and the reachability probe are components/screen.js, shared with the desktops.
//
// This page is the INTERACTIVE one (the right-rail dock is the read-only mirror): signing a
// session in is the reason the screen exists, and that needs a keyboard.

import { api } from "/static/api.js";
import { el, emptyState, skeleton } from "/static/util.js";
import { BROWSER, grantPass as grantScreenPass, screenSrc } from "/static/components/screen.js";

// The browser's own bindings of the shared screen builder (components/screen.js) — the dock and
// this page open the same screen, so they share these two rather than repeating the prefix.
export const grantPass = () => grantScreenPass(BROWSER);
export const frameSrc = ({ viewOnly = false } = {}) => screenSrc(BROWSER, { viewOnly });

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
      + "that address under Settings → Server → browser screen (noVNC) URL."),
      el("div", { class: "row mt" },
        el("a", { class: "btn small primary", href: "#/settings?section=server" }, "open Settings")));
    return;
  }
  try { await grantPass(); }
  catch (err) {
    // Without the pass every request the frame makes is refused, and the frame would render
    // the API's own 401 body at the user. Say so here instead.
    box.replaceChildren(emptyState("✕", "Couldn't get access to the screen",
      `${err.message} — the browser screen needs a pass from this console, and minting it failed.`));
    return;
  }
  const frame = el("iframe", { src: frameSrc(), class: "browser-screen",
    // the point of this page is to TYPE into the session (signing in), so it is not sandboxed
    // down to a picture — the read-only mirror in the rail is the one that is
    allow: "clipboard-read; clipboard-write" });
  box.replaceChildren(
    el("div", { class: "row", style: "gap:8px;align-items:center" },
      el("span", { class: "faint small" }, "relayed through this console · ",
        el("code", { class: "small" }, url)),
      // NOT a link to `url`. The upstream port answers 401 with "this browser port needs
      // `Authorization: Bearer <BROWSER_CDP_TOKEN>`", and a browser cannot attach a bearer
      // header to a top-level navigation — so an <a href> here could never open anything, which
      // is exactly what the operator reported (F632). It is the same mistake as F527/F530 in a
      // third place: a request the BROWSER makes carries none of the credentials the relay
      // supplies server-side. What someone diagnosing actually wants is the address, so hand
      // them that instead of a door that is bolted.
      el("button", { class: "btn small", type: "button",
        title: "the upstream needs a bearer token, so it cannot be opened from a browser tab — "
               + "the screen above is relayed through this console instead",
        onclick: async (ev) => {
          const btn = ev.currentTarget;
          try { await navigator.clipboard.writeText(url); btn.textContent = "copied"; }
          catch { btn.textContent = url; }           // no clipboard permission: show it to select
          setTimeout(() => { btn.textContent = "copy upstream address"; }, 2000);
        } }, "copy upstream address")),
    el("div", { class: "mt browser-screen-wrap" }, frame));
}
