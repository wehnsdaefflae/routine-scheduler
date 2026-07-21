// Practice-module picker — add or drop a routine's/conversation's traits AFTER creation.
//
// The traits/ directory is the state (see rsched/traits.py): checked = the routine holds
// that module. A later add copies the library text VERBATIM (only creation adapts), and an
// add reaches a run already in flight via control.json, so the checkbox takes effect on the
// current reply rather than the next one. Removal always lands at the next run — prose
// already in a live context cannot be unsaid.
//
// Shared by the routine page and the conversation header panel. Dumb by design: it paints
// and reports a diff; the caller owns the POST.

import { el, toast } from "/static/util.js";

// available: [{slug, summary, tags}] from GET /api/library · held: [slug]
// opts: {onSave(payload) -> Promise, base?: "routines"|"conversations", live?: boolean}
// Returns {node, value}: value() is {add, remove} against the ORIGINAL held set.
export function traitPicker(available, held, opts = {}) {
  const start = new Set(held || []);
  const now = new Set(start);
  const rows = el("div", {});
  const status = el("div", { class: "muted small", style: "margin-top:6px" });

  const paint = () => {
    const add = [...now].filter((s) => !start.has(s));
    const remove = [...start].filter((s) => !now.has(s));
    if (!add.length && !remove.length) {
      status.textContent = `${now.size} practice module${now.size === 1 ? "" : "s"} held`;
      save.disabled = true;
      return;
    }
    const bits = [];
    if (add.length) bits.push(`+${add.join(", +")}`);
    if (remove.length) bits.push(`−${remove.join(", −")}`);
    status.textContent = bits.join("  ") + (opts.live && add.length
      ? " — additions reach the run in flight" : "");
    save.disabled = false;
  };

  const save = el("button", { class: "btn", disabled: true, onclick: async () => {
    const payload = value();
    save.disabled = true;
    try {
      await opts.onSave?.(payload);
      payload.add.forEach((s) => start.add(s));
      payload.remove.forEach((s) => start.delete(s));
      toast(`practices updated (+${payload.add.length}/−${payload.remove.length})`);
    } catch (e) {
      toast(String(e?.message || e), 4000, { error: true });
    }
    paint();
  } }, "apply");

  for (const t of available || []) {
    const box = el("input", { type: "checkbox", "data-nopersist": true,
                              checked: now.has(t.slug) });
    box.onchange = () => { box.checked ? now.add(t.slug) : now.delete(t.slug); paint(); };
    rows.append(el("label", { class: "toggle-row" }, box,
      el("div", {}, el("div", { class: "t-title" }, t.slug),
        el("div", { class: "muted small" }, t.summary || ""))));
  }
  if (!(available || []).length) {
    rows.append(el("div", { class: "muted small" }, "the library carries no practice modules"));
  }

  const value = () => ({
    add: [...now].filter((s) => !start.has(s)),
    remove: [...start].filter((s) => !now.has(s)),
  });
  paint();
  return {
    node: el("div", { class: "traitpicker" }, rows,
      el("div", { class: "row", style: "gap:9px;align-items:center;margin-top:7px" },
        opts.onSave ? save : null, status)),
    get value() { return value(); },
  };
}
