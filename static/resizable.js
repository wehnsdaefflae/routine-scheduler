// Drag-to-resize + hide/show for the console's sidebars (operator request 2026-09-04): the main
// navigation rail, the page-nav, and the run view's left and right rails all share this one
// behaviour.
//
// THE INVARIANT that keeps the responsive layouts intact: this module writes a sidebar's width
// ONLY as a CSS custom property, and its hidden state ONLY as a class — never inline geometry on
// the sidebar itself. Each surface's stylesheet decides where that property is read, so a
// responsive `@media` collapse (the mobile bottom bar, the narrow icon rail) always stays
// authoritative: the width variable a user drags lives in a `*-set` property that only the WIDE
// layout consumes. An inline width on the element would win over a media query and defeat the
// collapse — this indirection is the whole point.
//
// One grip element per sidebar sits on its moving inner border and is BOTH controls at once: a
// DRAG resizes (persisted), a CLICK (no drag) hides or shows (persisted). Keyboard: the grip is
// focusable — Left/Right arrows resize, Enter/Space hide or show — so the feature is not
// mouse-only. Everything persists to localStorage per key and is restored by `restore()` before
// first paint of the surface.

import { storage } from "/static/util.js";

const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
const DRAG_THRESHOLD = 3;   // px of movement that turns a click into a drag

// wireSidebar(grip, opts) — attach resize+hide behaviour to `grip` for ONE sidebar.
//   key         identity for storage, e.g. "rail" | "runrail-left" | "runrail-right" | "pagenav"
//   scope       element the width var + hidden class are written on (default <html>)
//   cssVar      the width custom property the surface CSS reads, e.g. "--rail-w-set"
//   hiddenClass class toggled on `scope` when hidden, e.g. "sb-hidden-rail"
//   edge        "right" (sidebar body is LEFT of the grip) | "left" (body is RIGHT of the grip)
//   min,max     px clamp for the drag
//   measure     () => the sidebar's current rendered px width (seeds a drag)
//   step        keyboard arrow step in px (default 16)
// Returns { restore, toggleHidden, setWidth } — restore() applies stored state, call it once.
export function wireSidebar(grip, { key, scope = document.documentElement, cssVar, hiddenClass,
                                    edge = "right", min = 140, max = 560, measure, step = 16 }) {
  const wKey = `rsched_sb_${key}_w`;
  const hKey = `rsched_sb_${key}_hidden`;
  const setWidth = (px) => scope.style.setProperty(cssVar, `${Math.round(px)}px`);
  const curWidth = () => {
    const v = parseInt(getComputedStyle(scope).getPropertyValue(cssVar), 10);
    return Number.isNaN(v) ? (measure ? measure() : min) : v;
  };
  const isHidden = () => scope.classList.contains(hiddenClass);

  function toggleHidden(force) {
    const now = typeof force === "boolean"
      ? scope.classList.toggle(hiddenClass, force)
      : scope.classList.toggle(hiddenClass);
    storage.set(hKey, now ? "1" : "0");
    grip.setAttribute("aria-expanded", String(!now));
    return now;
  }

  function persistWidth() {
    storage.set(wKey, String(clamp(curWidth(), min, max)));
  }

  // ---- pointer drag (window-level tracking, so a fast drag off the 8px grip still follows) ----
  let drag = null;
  function onMove(e) {
    if (!drag) return;
    if (Math.abs(e.clientX - drag.startX) > DRAG_THRESHOLD) drag.moved = true;
    if (isHidden()) return;                     // a hidden rail is not resized, only re-shown
    const d = edge === "right" ? e.clientX - drag.startX : drag.startX - e.clientX;
    setWidth(clamp(drag.startW + d, min, max));
  }
  function onUp() {
    window.removeEventListener("pointermove", onMove);
    const moved = drag && drag.moved;
    drag = null;
    document.body.classList.remove("sb-dragging");
    if (moved && !isHidden()) persistWidth();
    else if (!moved) toggleHidden();            // a click (no drag) hides or shows
  }
  grip.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    drag = { startX: e.clientX, startW: measure ? measure() : curWidth(), moved: false };
    document.body.classList.add("sb-dragging");
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
    e.preventDefault();
  });

  // ---- keyboard a11y ----
  grip.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { toggleHidden(); e.preventDefault(); return; }
    if (isHidden()) return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      const dir = e.key === "ArrowRight" ? 1 : -1;
      const signed = edge === "right" ? dir : -dir;
      setWidth(clamp(curWidth() + signed * step, min, max));
      persistWidth();
      e.preventDefault();
    }
  });

  grip.setAttribute("role", "separator");
  grip.setAttribute("aria-orientation", "vertical");
  grip.setAttribute("aria-label", "resize sidebar — drag, or click to hide and show");
  if (!grip.hasAttribute("tabindex")) grip.tabIndex = 0;

  function restore() {
    const savedW = parseInt(storage.get(wKey) || "", 10);
    if (!Number.isNaN(savedW)) setWidth(clamp(savedW, min, max));
    toggleHidden(storage.get(hKey) === "1");
  }

  return { restore, toggleHidden, setWidth };
}
