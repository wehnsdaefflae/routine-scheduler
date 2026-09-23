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
//
// IT IS AN OVERLAY, so it only rests OPEN where there is margin to rest in. The console's
// reading column is `--rail-w` + `--shell-max` = 1452px wide, and the dock is pinned to the
// viewport's right edge. 1900px is the width at which a margin exists at all; below it an open
// dock sits ON the column, over the first lane rows' run-now buttons on Routines and over an
// endpoint card's save-key row in Settings. So below 1900px it opens COLLAPSED and a click-open
// is transient (it folds again on the next route change); above it, where the margin is real,
// the open/closed choice is remembered.

import { api } from "/static/api.js";
import { el, storage } from "/static/util.js";
// ONE builder for the relayed noVNC URL, shared with the full screen: two copies of that
// query shape would drift, and the `path` parameter is the load-bearing part.
import { frameSrc, grantPass } from "/static/views/browser.js";

const KEY_OPEN = "browser-dock-open";
const WIDE = "(min-width: 1900px)";

// Is there a SCREEN at the other end? The noVNC document, its assets and its whole chrome load
// whether or not the upstream is up — that is why a failed relay rendered noVNC's own red
// "Failed to connect to server" banner, its dark toolbar and an inert Connect button (the frame
// is pointer-events:none, so the button could not even be pressed) on EVERY page of the console.
// An HTTP probe of the document cannot see it.
//
// Nor can a probe that only watches the handshake: the relay ACCEPTS the socket before it dials
// websockify (api_browser_view.relay_socket), so `onopen` fires against a dead upstream too. The
// one unambiguous signal is a BYTE. RFB is server-first — a live screen sends its version banner
// (`RFB 003.008`) the instant the relay bridges the two sockets — so the first message means
// there is something to look at, and a close with nothing received means there is not. This is
// exactly the 1006 noVNC itself reports; the probe just reads it before an error can be painted.
// The socket is opened and closed BEFORE the frame's own, never alongside it, so a screen that
// admits one viewer at a time still gets a clean seat when the preview connects.
function relayReachable(timeoutMs = 6000) {
  return new Promise((resolve) => {
    const url = new URL("/browser-view/websockify", location.href);
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

  const wide = window.matchMedia(WIDE);
  // Open at rest only where the dock has a margin of its own to open into; the remembered
  // choice is a WIDE-screen choice, because at narrow widths there is no resting-open state to
  // remember. Below 1900px the dock always starts collapsed and one click opens it.
  let open = wide.matches && storage.get(KEY_OPEN) !== "0";
  slot.hidden = false;

  // Relayed through the console's own origin, exactly like the full screen (F527): the raw
  // upstream is http, and an https console may not open the ws:// socket noVNC needs, so a
  // direct embed showed a permanently blank preview. The ticket is for that socket.
  // The pass covers the document, noVNC's own asset fetches and the socket handshake alike
  // (F530). Without it every one of those is refused and the frame shows the API's 401 body —
  // which is exactly what the preview did on its first release.
  try { await grantPass(); }
  catch { return; }               // no pass, no preview: better absent than showing an error

  const title = el("a", { class: "bd-title", href: "#/browser",
    title: "open the full screen — that one takes the keyboard" }, "Browser");
  const toggle = el("button", { class: "bd-toggle", type: "button" });
  const head = el("div", { class: "bd-head" }, title, toggle);
  const body = el("div", { class: "bd-body" });
  slot.replaceChildren(head, body);

  const paint = () => {
    slot.classList.toggle("bd-collapsed", !open);
    body.hidden = !open;
    toggle.textContent = open ? "hide" : "show";
    toggle.title = open ? "collapse the preview" : "show the browser preview";
  };

  const mountFrame = () => {
    const frame = el("iframe", { class: "browser-dock-frame", title: "shared browser (read-only)",
      // view_only is noVNC's own switch; the CSS makes it inert regardless of what the page does
      src: frameSrc({ viewOnly: true }),
      tabindex: "-1", "aria-hidden": "true" });
    body.replaceChildren(frame,
      el("div", { class: "bd-note faint small" }, "read-only · click the title to take over"));
  };

  // Unreachable is a STATE OF THE SCREEN, not a failure of this page: say it once, quietly, in
  // the dock's own words, and offer the two things that can be done about it — look at the full
  // screen (which keeps noVNC's own diagnosis, where it belongs) or try again.
  const mountUnreachable = () => {
    const retry = el("button", { class: "btn small ghost", type: "button" }, "retry");
    retry.onclick = () => { retry.disabled = true; check(); };
    body.replaceChildren(el("div", { class: "bd-note faint small" },
      el("div", {}, "screen unreachable"),
      el("div", { class: "row", style: "gap:6px;margin-top:4px" },
        el("a", { class: "btn small ghost", href: "#/browser" }, "open the full page"), retry)));
  };

  const check = async () => {
    body.replaceChildren(el("div", { class: "bd-note faint small" }, "connecting…"));
    if (await relayReachable()) mountFrame();
    else mountUnreachable();
  };

  toggle.onclick = () => {
    open = !open;
    // A narrow console's dock is an overlay over live controls, so its open state is transient:
    // remembering it would park it on the Routines page's run-now column for good.
    if (wide.matches) storage.set(KEY_OPEN, open ? "1" : "0");
    paint();
  };

  // The #/browser page IS this screen, full size and interactive. Mirroring it in the corner
  // showed the operator the same failing frame twice.
  const onRoute = () => {
    const onBrowserPage = location.hash.startsWith("#/browser");
    slot.hidden = onBrowserPage;
    if (!onBrowserPage && !wide.matches && open) { open = false; paint(); }
  };
  window.addEventListener("hashchange", onRoute);

  paint();
  onRoute();
  await check();
}
