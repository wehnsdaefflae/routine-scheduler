// The remote-machine binding card (D102): the instance's SSH-host catalog as checkboxes, so
// a routine or a conversation binds the machines it may act on. Shared exactly like
// connections.js — a conversation is routine-shaped, and the engine's env injection
// (RSCHED_MACHINES / RSCHED_MACHINE_KEYS) reads routine.yaml `machines:` either way.
// `catalog` is the detail payload's `machine_catalog` (name/description/host/user/tags),
// `bound` its `machines` list; `onSave` receives the new name list and PATCHes the owner.
// With `onChange` instead of `onSave` it carries no button: every tick reports the whole list
// to a caller that keeps a draft (the routine page's settings form).
// A binding whose machine left the catalog stays visible so it can be cleared (it resolves
// to nothing at run time — the row says so).

import { el, toast, toastError } from "/static/util.js";

export function machinesCard(catalog, bound, { onSave, onChange }) {
  const checks = {};
  const current = () => Object.entries(checks).filter(([, cb]) => cb.checked).map(([n]) => n);
  const tick = (cb) => { if (onChange) cb.addEventListener("change", () => onChange(current())); };
  const box = el("div", {},
    // This card owns the section's ONE explanation: the routine page and the conversation
    // composer both mount it.
    el("div", { class: "muted small", style: "margin-bottom:8px" },
      "Which boxes from the instance's catalog this may reach over SSH, through the ",
      el("code", {}, "remote"), " util. A binding is a RESOURCE: the machines it names are the "
      + "only ones a run can act on. Takes effect at the next run. Add machines in ",
      el("a", { href: "#/settings?section=machines" }, "Settings → Machines"), "."));
  const boundSet = new Set(bound || []);
  if (!(catalog || []).length && !boundSet.size) {
    box.append(el("div", { class: "muted small" }, "no machines in the catalog yet"));
    return box;
  }
  for (const m of catalog || []) {
    const cb = el("input", { type: "checkbox" });
    if (boundSet.has(m.name)) cb.checked = true;
    checks[m.name] = cb;
    tick(cb);
    const meta = m.description || `${m.user}@${m.host}`;
    const tags = (m.tags || []).length ? ` [${m.tags.join(", ")}]` : "";
    box.append(el("label", { class: "row", style: "margin:5px 0;gap:8px;cursor:pointer" },
      cb, el("span", { style: "font-weight:600;min-width:110px" }, m.name),
      el("span", { class: "muted small" }, meta + tags)));
  }
  for (const name of boundSet) if (!(catalog || []).some((m) => m.name === name)) {
    const cb = el("input", { type: "checkbox", checked: "" });
    checks[name] = cb;
    tick(cb);
    box.append(el("label", { class: "row", style: "margin:5px 0;gap:8px;cursor:pointer" },
      cb, el("span", { style: "font-weight:600;min-width:110px" }, name),
      el("span", { class: "small", style: "color:var(--warn)" },
        "not in the catalog — uncheck to clear")));
  }
  if (onSave) {
    box.append(el("div", { class: "row mt" }, el("button", { class: "btn primary",
      onclick: async () => {
        try { await onSave(current()); toast("machines saved"); }
        catch (err) { toastError(err); }
      } }, "save machines")));
  }
  return box;
}
