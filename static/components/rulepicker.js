// General-rule picker — bind or unbind a routine's/conversation's rules AFTER creation.
//
// routine.yaml's `rules:` list is the state (see rsched/rules.py): checked = this rule binds
// the routine. Only the SET is per-routine — the prose lives once in the library, so editing
// it there reaches every holder. A newly bound rule reaches a run already in flight via
// control.json, so the checkbox takes effect on the current reply rather than the next one.
// Unbinding always lands at the next run — prose already in a live context cannot be unsaid.
//
// Shared by the routine page and the conversation header panel. Dumb by design: it paints
// and reports a diff; the caller owns the POST.

import { el, toast } from "/static/util.js";
import { docExpander } from "/static/components/docexpand.js";

// available: [{slug, summary, tags}] from GET /api/library · held: [slug]
// opts: {onSave(payload) -> Promise, base?: "routines"|"conversations", live?: boolean}
// Returns {node, value}: value() is {add, remove} against the ORIGINAL held set.
export function rulePicker(available, held, opts = {}) {
  const start = new Set(held || []);
  const now = new Set(start);
  const rows = el("div", {});
  const status = el("div", { class: "muted small", style: "margin-top:6px" });

  const paint = () => {
    const add = [...now].filter((s) => !start.has(s));
    const remove = [...start].filter((s) => !now.has(s));
    if (!add.length && !remove.length) {
      status.textContent = `${now.size} rule${now.size === 1 ? "" : "s"} bound`;
      save.disabled = true;
      return;
    }
    const bits = [];
    if (add.length) bits.push(`+${add.join(", +")}`);
    if (remove.length) bits.push(`−${remove.join(", −")}`);
    status.textContent = bits.join("  ") + (opts.live && add.length
      ? " — newly bound rules reach the run in flight" : "");
    save.disabled = false;
  };

  const save = el("button", { class: "btn", disabled: true, onclick: async () => {
    const payload = value();
    save.disabled = true;
    try {
      await opts.onSave?.(payload);
      payload.add.forEach((s) => start.add(s));
      payload.remove.forEach((s) => start.delete(s));
      toast(`rules updated (+${payload.add.length}/−${payload.remove.length})`);
    } catch (e) {
      toast(String(e?.message || e), 4000, { error: true });
    }
    paint();
  } }, "apply");

  for (const r of available || []) {
    const box = el("input", { type: "checkbox", "data-nopersist": true,
                              checked: now.has(r.slug) });
    box.onchange = () => { box.checked ? now.add(r.slug) : now.delete(r.slug); paint(); };
    const doc = docExpander("rules", r.slug);
    rows.append(el("div", { class: "rule-doc" },
      el("label", { class: "toggle-row" }, box,
        el("div", {}, el("div", { class: "t-title" }, r.slug),
          el("div", { class: "muted small" }, r.summary || ""),
          doc.btn)),
      doc.body));
  }
  if (!(available || []).length) {
    rows.append(el("div", { class: "muted small" }, "the library carries no general rules"));
  }

  const value = () => ({
    add: [...now].filter((s) => !start.has(s)),
    remove: [...start].filter((s) => !now.has(s)),
  });
  paint();
  return {
    node: el("div", { class: "rulepicker" }, rows,
      el("div", { class: "row", style: "gap:9px;align-items:center;margin-top:7px" },
        opts.onSave ? save : null, status)),
    get value() { return value(); },
  };
}
