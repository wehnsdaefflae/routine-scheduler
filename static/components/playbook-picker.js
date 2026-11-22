// The new-conversation screen's PLAYBOOK LIST (D307): the library's playbooks rendered as
// CARDS a person can read before clicking one.
//
// What it replaces: a `<select>` labelled "playbook" under the message box, whose options were
// bare titles — a playbook's `when` and `axis` appeared only AFTER it was picked, so somebody
// who did not already know playbooks existed had nothing to see. The operator decided
// (2026-11-21, code review) that starting from a playbook must be reachable where a person
// starts a conversation, which means READABLE without a click: title, the one-line `when`, the
// "varies:" axis and the tags, in the library's order.
//
// Two shapes this component owes the screen. A card is a real <button>, so it is reachable and
// operable from the keyboard with no handler of our own, and carries `aria-pressed` — the
// selection is a toggle, and a reader of the page is told which one is on. And with NO
// playbooks in the library nothing is mounted at all: the host stays empty rather than showing
// an empty box, because an empty box is a thing a person has to read and then dismiss.
//
// The selection itself is this component's (`.value`): it is what the <select> used to hold,
// and views/conversations-new.js posts it as the `playbook` form field unchanged.

import { el } from "/static/util.js";

//: the placeholder-switch notification, so the composer can say the playbook is the brief
const noop = () => {};

export function playbookPicker(onSelect = noop) {
  // No class on the host: an empty library must mount NOTHING, and a class-bearing wrapper
  // left behind is exactly the empty box the order rules out.
  const host = el("div", {});
  const slots = new Map();      // slug → the card's slot, which also holds the undo control
  let selected = "";

  const fresh = el("button", { type: "button", class: "btn small ghost pb-fresh",
    title: "forget this playbook and write the first message yourself" },
    "✕ start fresh instead");

  const paint = () => {
    for (const [slug, slot] of slots) {
      const card = slot.firstChild;
      const on = slug === selected;
      card.classList.toggle("pb-on", on);
      card.setAttribute("aria-pressed", on ? "true" : "false");
      if (on) slot.append(fresh);
    }
    if (!selected) fresh.remove();
  };

  const select = (slug) => {
    selected = slug;
    paint();
    onSelect(selected);
  };

  fresh.onclick = () => select("");

  const card = (p) => {
    const tags = (p.tags || []).filter(Boolean);
    const node = el("button", { type: "button", class: "pb-card", "data-pb": p.slug,
      "aria-pressed": "false", title: `start from the “${p.slug}” playbook` },
      el("span", { class: "pb-card-title" }, p.title || p.slug),
      p.when ? el("span", { class: "pb-card-when" }, p.when) : null,
      p.axis ? el("span", { class: "pb-card-axis" }, `varies: ${p.axis}`) : null,
      tags.length
        ? el("span", { class: "pb-card-tags" },
          ...tags.map((t) => el("span", { class: "pb-tag" }, t)))
        : null);
    node.onclick = () => select(p.slug === selected ? "" : p.slug);
    return el("div", { class: "pb-slot" }, node);
  };

  return {
    node: host,
    get value() { return selected; },

    /** Render the catalog `GET /api/playbooks` returned, in the order it came. */
    render(playbooks) {
      slots.clear();
      selected = "";
      // How many the catalog held, stamped on the host whether or not anything is mounted.
      // A browser case asserting the empty library renders NO list otherwise races this
      // render — `to_have_count(0)` passes the instant it is asked, so it can pass before the
      // `/api/playbooks` answer arrives and prove nothing (measured 2026-11-22: that case went
      // green through a neuter of the branch below, and red when the box was slow enough for
      // the render to land first). With the stamp the case waits for the render it is about.
      host.setAttribute("data-pb-count", String(playbooks.length));
      if (!playbooks.length) { host.replaceChildren(); return; }
      const list = el("div", { class: "pb-pick-list" });
      for (const p of playbooks) {
        const slot = card(p);
        slots.set(p.slug, slot);
        list.append(slot);
      }
      host.replaceChildren(el("div", { class: "pb-pick" },
        el("div", { class: "pb-pick-head" }, "Start from a playbook"),
        list));
    },
  };
}
