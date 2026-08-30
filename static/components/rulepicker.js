// General-rule picker — bind or unbind a routine's/conversation's rules AFTER creation.
//
// routine.yaml's `rules:` list is the state (see rsched/rules.py): bound = this rule binds the
// routine. Only the SET is per-routine — the prose lives once in the library, so editing it
// there reaches every holder. A newly bound rule reaches a run already in flight via
// control.json, so binding takes effect on the current reply; unbinding lands at the next run,
// because prose already in a live context cannot be unsaid.
//
// Shaped like the ability panel beside it, and for the same reason: twenty checkboxes in one
// undifferentiated list is a wall, and the reader's actual question is "what does this routine
// follow?" — not "which of these twenty exist?". So BOUND rules read as a short list of what
// the routine practises, and the rest is a catalogue below it, grouped by what the rules are
// FOR. Consistency between the two panels is itself the point: they answer the same shape of
// question and used to look nothing alike.
//
// Dumb by design: it paints and reports a diff; the caller owns the POST.

import { el, toast } from "/static/util.js";
import { docExpander } from "/static/components/docexpand.js";

// Rules cluster into a few jobs. A rule whose tags match none of these lands in "other" — the
// groups are a reading aid over library metadata, never a schema the library has to satisfy.
const GROUPS = [
  ["Working with you", ["conduct", "ask", "user", "communication", "teaching", "reporting"]],
  ["Getting it right", ["verification", "evidence", "review", "testing", "quality", "research"]],
  ["Changing things", ["git", "safety", "undo", "change", "authoring", "code", "error"]],
  ["Keeping a record", ["record-keeping", "memory", "decisions", "publishing", "web", "ui"]],
];

function groupFor(rule) {
  const tags = (rule.tags || []).map((t) => String(t).toLowerCase());
  for (const [name, keys] of GROUPS) {
    if (tags.some((t) => keys.some((k) => t.includes(k)))) return name;
  }
  return "Other";
}

// available: [{slug, summary, tags}] from GET /api/library · held: [slug]
// opts: {onSave(payload) -> Promise, live?: boolean}
// Returns {node, value}: value() is {add, remove} against the ORIGINAL held set.
export function rulePicker(available, held, opts = {}) {
  const start = new Set(held || []);
  const now = new Set(start);
  const all = available || [];
  const host = el("div", { class: "rulepicker" });
  const status = el("div", { class: "muted small" });

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
    render();
  } }, "apply");

  function paintStatus() {
    const { add, remove } = value();
    if (!add.length && !remove.length) {
      status.textContent = `${now.size} bound`;
      save.disabled = true;
      return;
    }
    const bits = [];
    if (add.length) bits.push(`+${add.join(", +")}`);
    if (remove.length) bits.push(`−${remove.join(", −")}`);
    status.textContent = bits.join("  ") + (opts.live && add.length
      ? " — newly bound rules reach the run in flight" : "");
    save.disabled = false;
  }

  // Sections are built from the COMMITTED set; a toggle only stages a change and marks the
  // row. Re-laying the panel out on every tick destroyed the control under the pointer and
  // threw away the one thing this panel is for — showing what you are about to change.
  const marks = [];        // [{slug, node}] — repainted on every toggle, rebuilt on save

  function repaint() {
    for (const { slug, node } of marks) {
      const staged = now.has(slug) !== start.has(slug);
      node.classList.toggle("pending", staged);
      node.classList.toggle("pending-drop", staged && start.has(slug));
    }
    paintStatus();
  }

  /** A bound rule: what the routine practises, with its full text one click away. */
  function boundRow(rule) {
    const doc = docExpander("rules", rule.slug);
    const box = el("input", { type: "checkbox", checked: "", "data-nopersist": true,
                              title: "unbind — takes effect at the next run" });
    const node = el("div", { class: "rule-bound", "data-rule": rule.slug },
      el("div", { class: "rule-line" }, box,
        el("span", { class: "rule-name" }, rule.slug),
        el("span", { class: "muted small prose" }, rule.summary || ""),
        doc.btn),
      doc.body);
    box.onchange = () => {
      if (box.checked) now.add(rule.slug); else now.delete(rule.slug);
      repaint();
    };
    marks.push({ slug: rule.slug, node });
    return node;
  }

  /** A catalogue row: name, one line, and a bind control. */
  function availRow(rule) {
    const box = el("input", { type: "checkbox", "data-nopersist": true });
    const node = el("label", { class: "avail-row", "data-rule": rule.slug }, box,
      el("span", { class: "avail-name" }, rule.slug),
      el("span", { class: "muted small prose" }, rule.summary || ""));
    box.onchange = () => {
      if (box.checked) now.add(rule.slug); else now.delete(rule.slug);
      repaint();
    };
    marks.push({ slug: rule.slug, node });
    return node;
  }

  function render() {
    const bound = all.filter((r) => start.has(r.slug));
    const rest = all.filter((r) => !start.has(r.slug));
    marks.length = 0;
    host.replaceChildren();
    if (!all.length) {
      host.append(el("div", { class: "muted small" }, "the library carries no general rules"));
      return;
    }
    host.append(el("div", { class: "lbl" }, `Practises · ${bound.length}`));
    host.append(bound.length
      ? el("div", { class: "rule-bounds" }, ...bound.map(boundRow))
      : el("div", { class: "muted small" },
          "this routine follows no general rules — it works from its recipe alone"));

    if (rest.length) {
      host.append(el("div", { class: "lbl mt" }, `Available · ${rest.length}`));
      const byGroup = new Map();
      for (const r of rest) {
        const g = groupFor(r);
        if (!byGroup.has(g)) byGroup.set(g, []);
        byGroup.get(g).push(r);
      }
      const order = [...GROUPS.map(([n]) => n), "Other"];
      for (const name of order) {
        const items = byGroup.get(name);
        if (!items) continue;
        host.append(el("div", { class: "rule-group" },
          el("div", { class: "rule-group-name" }, name),
          el("div", { class: "avail" }, ...items.map(availRow))));
      }
    }
    host.append(el("div", { class: "row mt", style: "gap:9px;align-items:center" },
      opts.onSave ? save : null, status));
    repaint();
  }

  const value = () => ({
    add: [...now].filter((s) => !start.has(s)),
    remove: [...start].filter((s) => !now.has(s)),
  });
  render();
  return {
    node: host,
    get value() { return value(); },
    // The FULL current selection, not the add/remove delta — what a pre-start composer
    // submits (F339). Without `onSave` the picker renders no apply button and is purely this.
    get selected() { return [...now]; },
  };
}
