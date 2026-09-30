// The settings page's two building blocks: a FIELD bound to the draft and a GROUP of sections
// with its own "more" menu.
//
// A field block wraps the control that edits one setting (or two edited together — a conduct
// doc and the capabilities it switches on are one decision). It builds the control from the
// DRAFT value and reports every edit back to the form; it rebuilds the control only when the
// value changes under it from outside (revert, "use the pattern's value", discard, an accept),
// because rebuilding on every edit destroys the control under the pointer.
//
// It also says, where the value sits, how the value stands:
//   CHANGED  (summons) — the draft differs from what the routine holds: it waits on a person,
//            the one who presses "accept changes". The proposal's reason rides beside it.
//   OVERRIDE (iris)    — the saved value departs from the routine's pattern. Structure, not
//            urgency: nothing waits on anyone; "use the pattern's value" is one click.
//
// A group is one labelled fold of the page. Its head says whether anything inside it changed or
// departs from the pattern — the fold is closed more often than not. A change hidden in a
// closed fold is a change nobody accepts on purpose. The rarely needed sections sit behind the
// group's own "more" menu, whose summary is a one-line digest of what is inside.

import { el, storage } from "/static/util.js";
import { describe } from "/static/components/settings-digest.js";

const asList = (keys) => (Array.isArray(keys) ? keys : [keys]);

/** Open every fold on the way to `node`, bring it to the middle of the screen, flash it. */
export function reveal(node, { flash = true } = {}) {
  if (!node) return false;
  for (let d = node.closest("details"); d; d = d.parentElement?.closest("details")) d.open = true;
  node.scrollIntoView({ block: "center" });
  if (flash) {
    node.classList.add("ref-flash");
    setTimeout(() => node.classList.remove("ref-flash"), 2500);
  }
  return true;
}

const READER_MOVES = ["wheel", "touchstart", "keydown", "pointerdown"];

/** Land on `node` as a page OPENS on it: reveal it, then HOLD it in place while the content
 *  around it settles. Sections above a target fill in after the page returns (a gate's check
 *  kinds, a schedule's next fire); each one pushed a landed heading back down the screen.
 *  The hold ends at the reader's first move, or after `holdMs`. */
export function landOn(root, node, { holdMs = 1500 } = {}) {
  if (!reveal(node)) return;
  const ro = new ResizeObserver(() => node.scrollIntoView({ block: "center" }));
  const stop = () => {
    ro.disconnect();
    for (const ev of READER_MOVES) removeEventListener(ev, stop, true);
  };
  for (const ev of READER_MOVES) addEventListener(ev, stop, { capture: true, passive: true });
  ro.observe(root);
  setTimeout(stop, holdMs);
}

/**
 * fieldBlock(form, keys, build, opts) → { node, redraw }
 *
 * `build(value, set)` returns the control. For one key `value` is that key's draft value; for a
 * list of keys it is `{key: value}` and `set` takes the same shape. `redraw()` rebuilds the
 * control from the current draft — for a caller whose OTHER inputs moved (a fresh detail read).
 */
export function fieldBlock(form, keys, build, opts = {}) {
  const list = asList(keys);
  const multi = Array.isArray(keys);
  const body = el("div", { class: "sf-body" });
  const notes = el("div", { class: "sf-notes" });
  const node = el("div", { class: "sf-field", "data-field": list.join(" "), ...opts.attrs },
    notes, body);
  let last = null;

  const current = () => (multi ? Object.fromEntries(list.map((k) => [k, form.get(k)]))
                               : form.get(keys));
  // The SAVED value is part of what a control was built from — a picker marks each staged row
  // against it — so an accept that moves it rebuilds the control even where the draft is equal.
  const fingerprint = (value = current()) => list.map((k) =>
    `${form.canon(k, multi ? (k in value ? value[k] : form.get(k)) : value)}\u0001`
    + form.canon(k, form.saved(k))).join("\u0000");
  // The edit is fingerprinted BEFORE it reaches the form: the form repaints every reader
  // synchronously. A reader that saw its own edit as a change from outside would rebuild the
  // very control that is still under the pointer.
  const report = (value) => {
    last = fingerprint(value);
    form.setMany(multi ? Object.fromEntries(list.filter((k) => k in value).map((k) => [k, value[k]]))
                       : { [keys]: value });
  };
  const draw = () => {
    last = fingerprint();
    body.replaceChildren(build(structuredClone(current()), report));
  };

  const label = (k) => opts.labels?.[k] || form.meta(k)?.label?.toLowerCase() || k;

  function changeNote(changed) {
    const reasons = changed.map((k) => form.reason(k)).filter(Boolean);
    const was = changed.length === 1 && !opts.noWas
      ? el("span", { class: "sf-was", title: describe(changed[0], form.saved(changed[0])) },
          "was ", describe(changed[0], form.saved(changed[0])))
      : null;
    return el("div", { class: "sf-note sf-note-change" },
      el("span", { class: "sf-mark" }, multi ? `changed · ${changed.map(label).join(", ")}`
                                             : "changed"),
      was,
      ...reasons.map((r) => el("span", { class: "sf-why prose" }, r)),
      el("button", { type: "button", class: "sf-act", "data-revert": list.join(" "),
        title: "put the saved value back",
        onclick: () => form.revert(changed) }, "revert"));
  }

  function overrideNote(over) {
    const p = form.pattern;
    const alreadyBack = over.every((k) => !form.draftOverride(k));
    return el("div", { class: "sf-note sf-note-override" },
      el("span", { class: "sf-mark" }, "override"),
      el("span", { class: "sf-pat" },
        `differs from ${p?.title || "its pattern"}`,
        ...over.map((k) => el("span", { class: "sf-patval", title: describe(k, form.patternValue(k)) },
          `— ${multi ? `its ${label(k)}` : "its value"}: `, describe(k, form.patternValue(k))))),
      alreadyBack
        ? el("span", { class: "sf-back" }, "matches the pattern once accepted")
        : el("button", { type: "button", class: "sf-act", "data-to-pattern": list.join(" "),
            title: "set the draft to the pattern's value — accept to keep it",
            onclick: () => form.toPattern(over) },
          "use the pattern's value"));
  }

  function paint() {
    if (fingerprint() !== last) draw();
    const changed = list.filter((k) => form.changed(k));
    const over = list.filter((k) => form.override(k));
    // a change that makes the routine depart from its pattern only once it is accepted
    const willDepart = list.filter((k) => !form.override(k) && form.draftOverride(k));
    node.classList.toggle("sf-changed", changed.length > 0);
    node.classList.toggle("sf-override", over.length > 0);
    notes.replaceChildren(...[
      changed.length ? changeNote(changed) : null,
      over.length ? overrideNote(over) : null,
      willDepart.length && changed.length
        ? el("div", { class: "sf-note sf-note-depart" },
            `departs from ${form.pattern?.title || "its pattern"} once accepted — its value: `
            + willDepart.map((k) => describe(k, form.patternValue(k))).join("; "))
        : null,
    ].filter(Boolean));
  }

  draw();
  paint();
  form.subscribe(paint);
  return { node, redraw: () => { draw(); paint(); } };
}

// ---- groups --------------------------------------------------------------------------------

// Which groups this browser keeps open — one key, the list of titles. `null` means "no choice
// yet", which is NOT "everything closed": an empty list is a legitimate answer.
const OPEN_KEY = "routine-settings-open";

function rememberedOpen() {
  const raw = storage.get(OPEN_KEY);
  if (raw === null) return null;
  try {
    const titles = JSON.parse(raw);
    return Array.isArray(titles) ? new Set(titles) : null;
  } catch { return null; }
}

function countLine(form, keys) {
  const changed = keys.filter((k) => form.changed(k)).length;
  const over = keys.filter((k) => form.override(k)).length;
  return [changed ? el("span", { class: "sf-count change", "data-group-changes": String(changed) },
                       `${changed} change${changed === 1 ? "" : "s"}`) : null,
          over ? el("span", { class: "sf-count override" },
                    `${over} override${over === 1 ? "" : "s"}`) : null].filter(Boolean);
}

/**
 * settingsGroup({ form, title, hint, keys, open, sections, more, moreKeys, digest }) → <details>
 *
 * `sections` and `more` are flat lists of nodes (settingsSection's [h2, panel] pairs, spread).
 * `keys` are every field the group edits; `moreKeys` those inside its "more" menu, whose summary
 * reads `digest()` — recomputed on every draft change, so it says what the menu holds NOW.
 */
export function settingsGroup({ form, title, hint, keys = [], open = false, sections = [],
                                more = [], moreKeys = [], digest = () => "" }) {
  const remembered = rememberedOpen();
  const counts = el("span", { class: "sf-counts" });
  const group = el("details", { class: "rgroup", "data-group": title,
    open: (remembered ? remembered.has(title) : open) ? true : null },
    el("summary", { class: "rgroup-head" },
      el("span", { class: "rgroup-title" }, title),
      hint ? el("span", { class: "rgroup-hint" }, hint) : null,
      counts),
    el("div", { class: "rgroup-body" }, ...sections, ...[moreMenu()].filter(Boolean)));

  function moreMenu() {
    if (!more.length) return null;
    const line = el("span", { class: "rmore-digest" });
    const mark = el("span", { class: "sf-counts" });
    const menu = el("details", { class: "rmore", "data-more": title },
      el("summary", { class: "rmore-head" }, el("span", { class: "rmore-word" }, "more"), line, mark),
      el("div", { class: "rmore-body" }, ...more));
    const paintMore = () => {
      line.textContent = digest();
      mark.replaceChildren(...countLine(form, moreKeys));
    };
    paintMore();
    form.subscribe(paintMore);
    return menu;
  }

  const paint = () => counts.replaceChildren(...countLine(form, keys));
  paint();
  form.subscribe(paint);
  // One write per toggle, read back off the DOM, so the stored list is exactly what the page
  // shows. `toggle` also fires when a jump opens the group, which is right: following an offer
  // into a group IS choosing to have it open.
  group.addEventListener("toggle", () => {
    const host = group.parentElement;
    if (!host) return;
    storage.set(OPEN_KEY, JSON.stringify(
      [...host.querySelectorAll(":scope > details.rgroup[open]")].map((d) => d.dataset.group)));
  });
  return group;
}

/** Every field key the page edits, grouped the way `patterns/fields.py` groups them. */
export function keysOf(form, ...groups) {
  return form.keys().filter((k) => groups.includes(form.meta(k)?.group));
}
