// A screen dock: a permanent, READ-ONLY corner preview of one relayed noVNC screen. The
// browser dock (browserdock.js) and the desktop dock (desktopdock.js) are both this — what they
// differ in is only WHICH screen they show and when there is one, so that is all they say.
//
// READ-ONLY IS ENFORCED, not implied: the frame carries `pointer-events: none` (base.css,
// `.sd-frame`) and is inert to the keyboard, so a passing click cannot steer a live session
// mid-run. The interactive screen is one click away on the dock's own page (its title), which is
// where typing belongs.
//
// A dock mounts into a slot inside #docks, a sibling of .workspace, so it survives view
// navigation — the frame is never re-created by routing, and the VNC connection therefore stays
// up instead of reconnecting on every page change. #docks is ONE fixed column for every piece of
// bottom-right chrome (base.css), so an open dock pushes its neighbours aside rather than lying
// on them.
//
// IT IS AN OVERLAY, so it only rests OPEN where there is margin to rest in. The console's
// reading column is `--rail-w` + `--shell-max` = 1452px wide, and the docks are pinned to the
// viewport's right edge. 1900px is the width at which a margin exists at all; below it an open
// dock sits ON the column, over the first lane rows' run-now buttons on Routines and over an
// endpoint card's save-key row in Settings. So below 1900px it opens COLLAPSED and a click-open
// is transient (it folds again on the next route change); above it, where the margin is real,
// the open/closed choice is remembered.
//
// IT CONNECTS THE FIRST TIME IT IS SEEN, never at load for its own sake. A screen may admit one
// viewer at a time, and a dock that dialled it at every console load — folded, hidden on its own
// page, or on a phone where it is not displayed at all — took that seat for a preview nobody was
// looking at (operator: "connect only when you first open it"). So it dials when it is first open
// AND on screen: at load only where it rests open, otherwise on the first click or on leaving its
// page. Once dialled it stays as it is — folding hides the frame rather than hanging up — until
// the screen it shows CHANGES (another desktop picked, or the one shown stopped).

import { el, storage } from "/static/util.js";
import { relayReachable, screenSrc } from "/static/components/screen.js";

const WIDE = "(min-width: 1900px)";

/**
 * Build a dock in `slot` and return its three controls:
 *   show({ key, token })  — the screen to preview (`key` names it; a new key re-dials)
 *   setPresent(bool)      — whether there is anything to preview at all (hidden when not)
 *   setSwitcher(node)     — an optional control beside the title (shown while open)
 * `page` is the full screen's route (the title's link; the dock hides there, since that page IS
 * the screen), `label` the title, `storageKey` where a wide console remembers open/closed.
 */
export function mountScreenDock(slot, { prefix, label, page, storageKey, frameTitle }) {
  const wide = window.matchMedia(WIDE);
  // Open at rest only where the dock has a margin of its own to open into; the remembered
  // choice is a WIDE-screen choice, because at narrow widths there is no resting-open state to
  // remember. Below 1900px the dock always starts collapsed and one click opens it.
  let open = wide.matches && storage.get(storageKey) !== "0";
  let present = true;
  let target = null;       // { key, token } — what the dock should show
  let dialled = null;      // the key of what its body shows now (or is connecting to)

  const title = el("a", { class: "sd-title", href: page,
    title: "open the full screen — that one takes the keyboard" }, label);
  const extra = el("span", { class: "sd-extra" });
  const toggle = el("button", { class: "sd-toggle", type: "button" });
  const head = el("div", { class: "sd-head" }, title, extra, toggle);
  const body = el("div", { class: "sd-body" });
  slot.replaceChildren(head, body);

  const paint = () => {
    slot.classList.toggle("sd-collapsed", !open);
    body.hidden = !open;
    extra.hidden = !open;
    toggle.textContent = open ? "hide" : "show";
    toggle.title = open ? "collapse the preview" : `show the ${label.toLowerCase()} preview`;
  };

  const mountFrame = (t) => {
    const frame = el("iframe", { class: "sd-frame", title: frameTitle,
      // view_only is noVNC's own switch; the CSS makes it inert regardless of what the page does
      src: screenSrc(prefix, { viewOnly: true, token: t.token }),
      tabindex: "-1", "aria-hidden": "true" });
    body.replaceChildren(frame,
      el("div", { class: "sd-note faint small" }, "read-only · click the title to take over"));
  };

  // Unreachable is a STATE OF THE SCREEN, not a failure of this page: say it once, quietly, in
  // the dock's own words, and offer the two things that can be done about it — look at the full
  // screen (which keeps noVNC's own diagnosis, where it belongs) or try again.
  const mountUnreachable = () => {
    const retry = el("button", { class: "btn small ghost", type: "button" }, "retry");
    retry.onclick = () => { retry.disabled = true; check(); };
    body.replaceChildren(el("div", { class: "sd-note faint small" },
      el("div", {}, "screen unreachable"),
      el("div", { class: "row", style: "gap:6px;margin-top:4px" },
        el("a", { class: "btn small ghost", href: page }, "open the full page"), retry)));
  };

  const check = async () => {
    const t = target;
    body.replaceChildren(el("div", { class: "sd-note faint small" }, "connecting…"));
    const ok = await relayReachable(prefix, { token: t.token });
    if (target !== t) return;               // superseded while probing: the newer dial paints
    if (ok) mountFrame(t);
    else mountUnreachable();
  };

  // The first sight of a screen is its one dial (see the head of this file): what it found — a
  // frame, or "unreachable" with its retry — is what every later open shows, until the screen
  // the dock is asked to show changes.
  const connectOnSight = () => {
    if (!target || !open || slot.hidden || dialled === target.key) return;
    dialled = target.key;
    check();
  };

  const onPage = () => location.hash.startsWith(page);

  toggle.onclick = () => {
    open = !open;
    // A narrow console's dock is an overlay over live controls, so its open state is transient:
    // remembering it would park it on the Routines page's run-now column for good.
    if (wide.matches) storage.set(storageKey, open ? "1" : "0");
    paint();
    connectOnSight();
  };

  // The dock's own page IS this screen, full size and interactive. Mirroring it in the corner
  // showed the operator the same failing frame twice.
  const onRoute = () => {
    slot.hidden = onPage() || !present;
    if (!onPage() && !wide.matches && open) { open = false; paint(); }
    connectOnSight();
  };
  window.addEventListener("hashchange", onRoute);

  paint();
  onRoute();

  return {
    show(next) {
      if (target && next && target.key === next.key) return;
      target = next;
      // a screen that is no longer the one to show must not keep its connection open behind a
      // folded dock: drop it now, and dial the new one the moment it can be seen
      if (dialled !== null && dialled !== next?.key) { dialled = null; body.replaceChildren(); }
      connectOnSight();
    },
    setPresent(value) {
      present = value;
      slot.hidden = onPage() || !present;
      connectOnSight();
    },
    setSwitcher(node) {
      // the same node again is a no-op: re-inserting an open <select> would snap it shut
      if (node ? extra.childNodes.length === 1 && extra.firstChild === node : !extra.firstChild) {
        return;
      }
      extra.replaceChildren(...(node ? [node] : []));
    },
  };
}
