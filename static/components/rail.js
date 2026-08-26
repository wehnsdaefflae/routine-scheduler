// The side rail — ONE component behind the run view and the conversation view.
//
// R341 (user steer on R339 + R340: "don't we reuse the same code?"): the two views had
// divergent copies. The conversation rail grew per-section collapse remembered per browser
// (F296, R262 pt1); the run rail kept plain caption divs, so the run view's State / Tasks /
// Files / Artifacts sections could not be collapsed at all. Both now render this.
//
// A section is a caption plus one or more bodies. The caption is the toggle (click, Enter or
// Space); the open/closed state lives in localStorage under `rail:<name>`, so it is shared
// across the two views on purpose — a person who folds "tasks" away means it in both.
//
// Sections can be added after construction (the conversation rail reveals `browser` and
// `background` only once there is something in them) and hidden again with `toggle`.

import { el } from "/static/util.js";

const KEY = (name) => `rail:${name}`;

export function createRail(container, { sections = [] } = {}) {
  const caps = new Map();      // name → {cap, bodies}

  const isClosed = (name) => localStorage.getItem(KEY(name)) === "closed";

  function apply(name) {
    const rec = caps.get(name);
    if (!rec) return;
    const closed = isClosed(name);
    rec.cap.classList.toggle("closed", closed);
    for (const b of rec.bodies) b.hidden = closed || rec.hidden;
    rec.cap.hidden = rec.hidden;
  }

  /** Append one collapsible section; returns its bodies so the caller can fill them. */
  function add(name, ...bodies) {
    const cap = el("div", { class: "rail-cap", role: "button", tabindex: "0",
                            "data-rail": name, title: "collapse / expand" }, name);
    const flip = () => {
      localStorage.setItem(KEY(name), isClosed(name) ? "open" : "closed");
      apply(name);
    };
    cap.onclick = flip;
    cap.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); flip(); }
    };
    caps.set(name, { cap, bodies, hidden: false });
    container.append(cap, ...bodies);
    apply(name);
    return bodies.length === 1 ? bodies[0] : bodies;
  }

  /** Show/hide a whole section — for rails whose sections appear only when populated. */
  function toggle(name, visible) {
    const rec = caps.get(name);
    if (!rec) return;
    rec.hidden = !visible;
    apply(name);
  }

  for (const s of sections) add(s.name, ...(s.bodies || [el("div", {})]));
  return { add, toggle, has: (name) => caps.has(name) };
}
