// Drag-and-drop for the dashboard week strip (weekgrid.js). One pointer gesture, four moves —
// resolved by DROP TARGET, so every drag reads the same way:
//
//   · onto a SIBLING bar in the same lane  → reorder the group (before/after by bar half)
//   · onto ANOTHER group's lane            → join that group (leaving the current one, if any)
//   · onto the remove strip below          → leave the group
//   · along the OWN lane's empty space     → reschedule — the GROUP's cron on a scheduled-group
//                                            lane, the routine's own cron otherwise (5-min snap)
//
// One-shot bars are not draggable (re-arming is the Schedule-once card's job). The controller
// owns only the gesture: geometry, ghost, tip, target resolution. The semantic ops arrive as
// HANDLERS from the view — { reorder(group, slug, targetSlug, after), join(group, slug,
// fromGroup), leave(group, slug), reschedule(slug, when), rescheduleGroup(group, when) } — each
// async, each ending in a data reload that redraws the strip from truth. While a gesture is
// live, weekgrid holds its re-renders (active()), so the bar never vanishes mid-drag.

import { el } from "/static/util.js";

const DRAG_MIN_PX = 5;        // pointer travel before a press becomes a drag (below: a click)
const SNAP_MS = 5 * 60_000;   // reschedule drops snap to 5 minutes
const BAR_PAD = 4;            // widen skinny bars' hit range for sibling targeting (svg px)

const fmtAt = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });

export function weekDrag(host, handlers) {
  let layout = null;          // { svg, rows: [{lane, y, rowbg}], hits, t0, span, W, headH, rowH }
  let gesture = null;         // live drag state, null when idle
  let suppressClick = false;  // a completed drag must not fire the bar's <a> navigation

  const tip = el("div", { class: "wg-dragtip", hidden: true });
  const zone = el("div", { class: "wg-dropzone", hidden: true },
    "⏏ drop here to remove from its group");

  // A drag that ends on an <a>-wrapped bar would also navigate — swallow that one click.
  host.addEventListener("click", (e) => {
    if (!suppressClick) return;
    suppressClick = false;
    e.preventDefault();
    e.stopPropagation();
  }, true);

  function setLayout(l) {
    layout = l;
    host.append(tip, zone);   // re-adopt the overlays after weekgrid's replaceChildren()
    l.svg.addEventListener("pointerdown", down);
  }

  const toSvg = (e) => {
    const r = layout.svg.getBoundingClientRect();
    const k = layout.W / r.width;   // uniform scale (viewBox width ↔ css width)
    return { sx: (e.clientX - r.left) * k, sy: (e.clientY - r.top) * k };
  };
  const geom = (h) => ({ x: parseFloat(h.rect.getAttribute("x")),
                         w: parseFloat(h.rect.getAttribute("width")),
                         h: parseFloat(h.rect.getAttribute("height")) });
  const laneAt = (sy) => {
    const i = Math.floor((sy - layout.headH) / layout.rowH);
    return i >= 0 && i < layout.rows.length ? i : -1;
  };
  const barUnder = (laneIdx, sx, notSlug) => {
    let best = null, bestD = Infinity;
    for (const h of layout.hits) {
      if (h.laneIdx !== laneIdx || h.slug === notSlug) continue;
      const g = geom(h);
      if (sx < g.x - BAR_PAD || sx > g.x + g.w + BAR_PAD) continue;
      const d = Math.abs(sx - (g.x + g.w / 2));
      if (d < bestD) { bestD = d; best = h; }
    }
    return best;
  };
  const snap = (ms) => Math.round(ms / SNAP_MS) * SNAP_MS;

  function down(e) {
    if (e.button !== 0 || gesture || !layout) return;
    const hit = layout.hits.find((h) => h.rect === e.target);
    if (!hit) return;
    const { sx, sy } = toSvg(e);
    gesture = { hit, x0: e.clientX, y0: e.clientY, sx0: sx, sy0: sy, live: false, action: null };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", key);
  }

  function begin() {
    const { hit } = gesture;
    gesture.live = true;
    host.classList.add("wg-dragging");
    gesture.ghost = hit.rect.cloneNode(false);
    gesture.ghost.classList.add("wg-ghost");
    layout.svg.append(gesture.ghost);
    tip.hidden = false;
    const from = layout.rows[hit.laneIdx].lane.group;
    if (from) {   // leaving is only meaningful for a grouped bar
      const hostR = host.getBoundingClientRect(), svgR = layout.svg.getBoundingClientRect();
      zone.style.top = `${svgR.bottom - hostR.top}px`;
      zone.style.height = `${Math.max(28, hostR.bottom - svgR.bottom)}px`;
      zone.hidden = false;
    }
  }

  // What would dropping HERE do? One resolver feeds both the live tip and the drop itself.
  function resolve(e) {
    const { hit } = gesture;
    const src = layout.rows[hit.laneIdx].lane;
    if (!zone.hidden) {
      const zr = zone.getBoundingClientRect();
      if (e.clientY >= zr.top && e.clientY <= zr.bottom && e.clientX >= zr.left && e.clientX <= zr.right)
        return { type: "leave", group: src.group,
                 tip: `${hit.name} → leave ⛓ ${src.group.name}` };
    }
    const { sx, sy } = toSvg(e);
    const t = laneAt(sy);
    if (t < 0) return { type: "none", tip: "✕" };
    const target = layout.rows[t].lane;
    if (t !== hit.laneIdx) {
      if (target.group && !target.group.members.includes(hit.slug))
        return { type: "join", group: target.group, from: src.group,
                 tip: `${hit.name} → join ⛓ ${target.group.name}` };
      return { type: "none", tip: "✕" };
    }
    const sib = src.group ? barUnder(t, sx, hit.slug) : null;
    if (sib) {
      const g = geom(sib);
      const after = sx > g.x + g.w / 2;
      return { type: "reorder", group: src.group, targetSlug: sib.slug, after,
               tip: `${hit.name} → ${after ? "after" : "before"} ${sib.name} in ⛓ ${src.group.name}` };
    }
    // empty same-lane space: move in time
    const dt = ((sx - gesture.sx0) / layout.W) * layout.span;
    if (src.group && src.group.cron) {
      const when = new Date(snap((hit.fireT ?? hit.start) + dt));
      return { type: "reschedule-group", group: src.group, when,
               tip: `⛓ ${src.group.name} → ${fmtAt.format(when)}` };
    }
    const when = new Date(snap(hit.start + dt));
    return { type: "reschedule", when, tip: `${hit.name} → ${fmtAt.format(when)}` };
  }

  function move(e) {
    if (!gesture) return;
    if (!gesture.live) {
      if (Math.hypot(e.clientX - gesture.x0, e.clientY - gesture.y0) < DRAG_MIN_PX) return;
      begin();
    }
    e.preventDefault();
    const action = gesture.action = resolve(e);
    // ghost: follows time along the own lane, snaps onto the target lane for membership moves
    const { sx, sy } = toSvg(e);
    const g0 = geom(gesture.hit);
    gesture.ghost.setAttribute("x", sx - (gesture.sx0 - g0.x));
    const t = laneAt(sy);
    const rowY = t >= 0 ? layout.rows[t].y : layout.rows[gesture.hit.laneIdx].y;
    gesture.ghost.setAttribute("y", rowY + layout.rowH / 2 - g0.h / 2);
    for (const r of layout.rows)
      r.rowbg.classList.toggle("wg-drop-ok",
        (action.type === "join" && r.lane.group === action.group)
        || (action.type === "reorder" && r.lane === layout.rows[gesture.hit.laneIdx].lane));
    zone.classList.toggle("armed", action.type === "leave");
    tip.textContent = action.tip;
    const hostR = host.getBoundingClientRect();
    tip.style.left = `${e.clientX - hostR.left + 14}px`;
    tip.style.top = `${e.clientY - hostR.top - 26}px`;
  }

  function up(e) {
    if (!gesture) return;
    const { live, action, hit } = gesture;
    cleanup();
    if (!live) return;            // a plain click — let the bar's link do its thing
    suppressClick = true;
    if (!action || action.type === "none") return;
    if (action.type === "reorder") handlers.reorder(action.group, hit.slug, action.targetSlug, action.after);
    else if (action.type === "join") handlers.join(action.group, hit.slug, action.from);
    else if (action.type === "leave") handlers.leave(action.group, hit.slug);
    else if (action.type === "reschedule-group") handlers.rescheduleGroup(action.group, action.when);
    else if (action.type === "reschedule") handlers.reschedule(hit.slug, action.when);
  }

  function key(e) {
    if (e.key === "Escape") cancel();
  }

  function cancel() {
    if (gesture?.live) suppressClick = true;
    cleanup();
  }

  function cleanup() {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", cancel);
    window.removeEventListener("keydown", key);
    gesture?.ghost?.remove();
    for (const r of layout?.rows || []) r.rowbg.classList.remove("wg-drop-ok");
    host.classList.remove("wg-dragging");
    tip.hidden = true;
    zone.hidden = true;
    zone.classList.remove("armed");
    gesture = null;
  }

  return { setLayout, active: () => !!gesture?.live };
}
