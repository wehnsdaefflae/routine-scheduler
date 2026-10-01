// General-rule picker — bind or unbind a routine's/conversation's rules.
//
// routine.yaml's `rules:` list is the state (see rsched/rules.py): bound = this rule binds the
// routine. Only the SET is per-routine — the prose lives once in the library, so editing it
// there reaches every holder. A SAVE reaches a run already in flight in both directions, at
// its next turn boundary, via control.json (engine/switches.py): a bound rule's prose is
// appended, an unbound one is declared no longer binding. Its TEXT stays in the context unless
// the reader also asks to withdraw it, which rewrites the conversation (see `eraseBox`).
//
// Shaped like the ability panel beside it, and for the same reason: twenty checkboxes in one
// undifferentiated list is a wall, and the reader's actual question is "what does this routine
// follow?" — not "which of these twenty exist?". So BOUND rules read as a short list of what
// the routine practises, and the rest is a catalogue below it, grouped by what the rules are
// FOR. Consistency between the two panels is itself the point: they answer the same shape of
// question and used to look nothing alike.
//
// Dumb by design: it paints and reports a diff; the caller owns the POST.

import { effectLine } from "/static/components/effectline.js";
import { el, toast, toastError } from "/static/util.js";
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
// opts: {onSave(payload) -> Promise, live?: boolean} — `live`: a run is in flight that a save
//       reaches (the conversation header's reply)
//    or {onChange(selected), saved: [slug]} — a DRAFT picker (the routine page's settings form):
//       no apply button, every tick reports the full selection, and a row is marked staged
//       against `saved` (what the routine holds) rather than against `held` (the draft).
// Returns {node, value}: value() is {add, remove} against the ORIGINAL held set.
export function rulePicker(available, held, opts = {}) {
  const start = new Set(opts.saved || held || []);
  const now = new Set(held || []);
  let ready = false;                 // building the picker reports nothing
  const all = available || [];
  const host = el("div", { class: "rulepicker" });
  const status = el("div", { class: "muted small" });
  // Only a picker that SAVES reaches a run in flight: its POST signals the run's control.json.
  // A draft's selection lands with the page's accept, which a live run refuses and which carries
  // no erase — offering either there was a control that did nothing beside a false sentence.
  const live = !!(opts.live && opts.onSave);

  // Withdrawing the TEXT is a separate, dearer decision from withdrawing the rule's
  // authority: it rewrites the messages carrying it, which costs the provider's prompt cache
  // from that point on. Offered only beside a staged unbind on a live run, because otherwise
  // there is no context to withdraw anything from.
  const eraseBox = el("input", { type: "checkbox", "data-nopersist": true });
  const eraseLabel = el("label", { class: "rule-erase", hidden: true },
    eraseBox,
    el("span", {}, "also withdraw their text from the running context"),
    el("span", { class: "muted small" },
      " — telling the run they no longer bind is enough on its own; erasing rewrites the "
      + "conversation and loses the prompt cache from that point, which costs tokens and "
      + "latency on the next turn"));

  const save = el("button", { class: "btn", disabled: true, onclick: async () => {
    const payload = { ...value(), erase: eraseBox.checked };
    save.disabled = true;
    try {
      await opts.onSave?.(payload);
      payload.add.forEach((s) => start.add(s));
      payload.remove.forEach((s) => start.delete(s));
      toast(`rules updated (+${payload.add.length}/−${payload.remove.length})`);
    } catch (e) {
      toastError(e);
    }
    render();
  } }, "apply");

  function paintStatus() {
    const { add, remove } = value();
    // The erase offer stands beside a staged unbind on a live run and nowhere else. Hidden, it
    // is unticked too: it used to outlive the unbind it was for — withdrawn, or saved — still
    // ticked, so the next unbind erased text the reader never chose to withdraw.
    eraseLabel.hidden = !(live && remove.length);
    if (eraseLabel.hidden) eraseBox.checked = false;
    if (!add.length && !remove.length) {
      status.textContent = `${now.size} bound`;
      save.disabled = true;
      return;
    }
    const bits = [];
    if (add.length) bits.push(`+${add.join(", +")}`);
    if (remove.length) bits.push(`−${remove.join(", −")}`);
    status.textContent = bits.join("  ") + (live
      ? " — reaches the run in flight at its next turn" : "");
    save.disabled = false;
  }

  // Sections are built from the COMMITTED set; a toggle only stages a change and marks the
  // row. Re-laying the panel out on every tick destroyed the control under the pointer and
  // threw away the one thing this panel is for — showing what you are about to change.
  const marks = [];        // [{slug, node}] — repainted on every toggle, rebuilt on save

  // Both directions reach a live run at its next turn boundary (an unbind used to wait for
  // the next run; engine/switches.apply_rule_drop made it symmetric). Without a live run the
  // change simply lands with the save, or with the page's accept.
  const WHEN = live ? " — takes effect on the next turn, this run included" : "";
  const WILL_BIND = `will bind${WHEN}`;
  const WILL_DROP = `will unbind${WHEN}`;

  function repaint() {
    for (const { slug, node, why } of marks) {
      const staged = now.has(slug) !== start.has(slug);
      const dropping = staged && start.has(slug);
      node.classList.toggle("pending", staged);
      node.classList.toggle("pending-drop", dropping);
      if (why) why.textContent = staged ? (dropping ? WILL_DROP : WILL_BIND) : "";
    }
    paintStatus();
    if (ready) opts.onChange?.([...now]);
  }

  /** A bound rule: what the routine practises, with its full text one click away. */
  function boundRow(rule) {
    const doc = docExpander("rules", rule.slug);
    const box = el("input", { type: "checkbox", checked: now.has(rule.slug) ? "" : null,
                              "data-nopersist": true, title: "untick to unbind" });
    const why = el("span", { class: "rule-why small" });
    const node = el("div", { class: "rule-bound", "data-rule": rule.slug },
      el("div", { class: "rule-line" }, box,
        el("span", { class: "rule-name" }, rule.slug),
        effectLine(rule, true),
        doc.btn),
      why, doc.body);
    box.onchange = () => {
      if (box.checked) now.add(rule.slug); else now.delete(rule.slug);
      repaint();
    };
    marks.push({ slug: rule.slug, node, why });
    return node;
  }

  /** A catalogue row: name, one line, and a bind control. */
  function availRow(rule) {
    const box = el("input", { type: "checkbox", checked: now.has(rule.slug) ? "" : null,
                              "data-nopersist": true });
    const why = el("span", { class: "rule-why small" });
    const node = el("label", { class: "avail-row", "data-rule": rule.slug }, box,
      el("span", { class: "avail-name" }, rule.slug),
      effectLine(rule, false), why);
    box.onchange = () => {
      if (box.checked) now.add(rule.slug); else now.delete(rule.slug);
      repaint();
    };
    marks.push({ slug: rule.slug, node, why });
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
    host.append(
      el("div", { class: "lbl" }, `Practises · ${bound.length}`),
      el("div", { class: "muted small prose", style: "margin:-4px 0 9px" },
        "Every run's digest names each practised rule with the moment it applies; the run "
        + "reads the prose from the single copy in the library when that moment comes. The "
        + "prose is never pasted into the prompt, so binding one costs nothing until it is "
        + "needed. A run may read ANY rule at any time; binding is what makes one standing."));
    host.append(bound.length
      ? el("div", { class: "rule-bounds" }, ...bound.map(boundRow))
      : el("div", { class: "muted small" },
          "this routine follows no general rules — it works from its recipe alone"));

    if (rest.length) {
      // the unbound half of the library: twenty-five cards with their ON/OFF/WHEN lines, which
      // on a phone is the panel. Open on a wide screen, a disclosure on a narrow one — the
      // cards themselves are untouched either way.
      const wide = window.matchMedia("(min-width: 861px)").matches;
      const availHost = wide ? host : el("div", {});
      host.append(wide
        ? el("div", { class: "lbl mt" }, `Available · ${rest.length}`)
        : el("details", { class: "mt avail-fold" },
            el("summary", { class: "lbl" }, `Available · ${rest.length}`), availHost));
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
        availHost.append(el("div", { class: "rule-group" },
          el("div", { class: "rule-group-name" }, name),
          el("div", { class: "avail" }, ...items.map(availRow))));
      }
    }
    host.append(el("div", { class: "row mt", style: "gap:9px;align-items:center" },
      opts.onSave ? save : null, status));
    host.append(eraseLabel);
    repaint();
  }

  const value = () => ({
    add: [...now].filter((s) => !start.has(s)),
    remove: [...start].filter((s) => !now.has(s)),
  });
  render();
  ready = true;
  return {
    node: host,
    get value() { return value(); },
    // The FULL current selection, not the add/remove delta — what a pre-start composer
    // submits (F339). Without `onSave` the picker renders no apply button and is purely this.
    get selected() { return [...now]; },
  };
}
