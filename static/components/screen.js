// A relayed noVNC screen, as the console's pages and docks open one — the shared browser's
// (`browser-view`) and every agent desktop's (`desktop-view`). ONE builder for all of them: the
// query shape below is load-bearing (`path` decides where the socket goes), and two copies of
// it would drift. The server half is web/screen_proxy.py + web/api_screen_view.py; the prefixes
// are spelled there too.
//
// EVERY SCREEN LOADS THROUGH THE CONSOLE'S OWN ORIGIN (F527), never from the configured address:
// a page served over https may not open the insecure `ws://` socket noVNC needs, so an embedded
// raw screen stayed blank exactly where the operator uses the console. Same-origin, the socket
// inherits the console's TLS by itself.

import { api } from "/static/api.js";

export const BROWSER = "browser-view";
export const DESKTOP = "desktop-view";

// Mint a screen's PASS before any frame is created (F530). An <iframe src> is a naked GET, and
// noVNC then fetches its OWN siblings (app/ui.js, app/styles/base.css, the images) with URLs it
// builds itself — so no parameter chosen here reaches them, and the first shipped version, which
// put a ticket in the URL, rendered this app's own 401 body inside the frame. A cookie is the
// one credential a browser attaches to a frame's sub-resources unasked; each screen's is scoped
// to its own relay path, so a browser pass opens no desktop.
export async function grantPass(prefix) {
  await api(`/api/${prefix}/pass`, { method: "POST", body: {} });
}

// The socket noVNC opens, relative to the page it was loaded from — this console. A desktop's
// carries the screen token the fleet reported for it: one websockify serves every desktop and
// the token picks which (the relay proves its shape before it forwards it).
function socketPath(prefix, token) {
  return token ? `${prefix}/websockify?token=${encodeURIComponent(token)}` : `${prefix}/websockify`;
}

// The frame's address. No credential rides in it: the pass cookie covers the document, its
// assets and the socket handshake alike.
//
// `host` (and `port`, when the console is not on its scheme's default) are named explicitly,
// although they are this console's own: that is what makes `path` mean the same thing to every
// noVNC the sidecars ship. 1.3 (the browser's) builds `ws://<host>:<port>/` + path, defaulting
// host to the page's; 1.6 (the desktops') defaults host to EMPTY and then resolves `path`
// against the frame's own URL — so `desktop-view/websockify` from `/desktop-view/vnc.html` became
// `/desktop-view/desktop-view/websockify`, a socket no route serves. With a host named, 1.6
// resolves `./<path>` from the root, exactly as 1.3 does. The scheme is left to noVNC's own
// default (`encrypt` = the page is https), which is the console's in both.
export function screenSrc(prefix, { viewOnly = false, token = "" } = {}) {
  const q = new URLSearchParams({ autoconnect: "1", resize: "scale", host: location.hostname });
  if (location.port) q.set("port", location.port);
  q.set("path", socketPath(prefix, token));
  if (viewOnly) q.set("view_only", "1");
  return `/${prefix}/vnc.html?${q}`;
}

// Is there a SCREEN at the other end? The noVNC document, its assets and its whole chrome load
// whether or not the upstream is up — that is why a failed relay rendered noVNC's own red
// "Failed to connect to server" banner, its dark toolbar and an inert Connect button (a dock's
// frame is pointer-events:none, so the button could not even be pressed) on EVERY page of the
// console. An HTTP probe of the document cannot see it.
//
// Nor can a probe that only watches the handshake: the relay ACCEPTS the socket before it dials
// websockify (api_screen_view._bridge), so `onopen` fires against a dead upstream too. The one
// unambiguous signal is a BYTE. RFB is server-first — a live screen sends its version banner
// (`RFB 003.008`) the instant the relay bridges the two sockets — so the first message means
// there is something to look at, and a close with nothing received means there is not. This is
// exactly the 1006 noVNC itself reports; the probe just reads it before an error can be painted.
// The socket is opened and closed BEFORE the frame's own, never alongside it, so a screen that
// admits one viewer at a time still gets a clean seat when the preview connects.
export function relayReachable(prefix, { token = "", timeoutMs = 6000 } = {}) {
  return new Promise((resolve) => {
    const url = new URL(`/${socketPath(prefix, token)}`, location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    let sock;
    let settled = false;
    const done = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { sock?.close(); } catch { /* already gone */ }
      resolve(ok);
    };
    const timer = setTimeout(() => done(false), timeoutMs);
    try { sock = new WebSocket(url, "binary"); } catch { done(false); return; }
    sock.binaryType = "arraybuffer";
    sock.onmessage = () => done(true);   // the RFB banner: there is a screen behind the relay
    sock.onerror = () => done(false);
    sock.onclose = () => done(false);
  });
}
