// ABILITY CARDS — one card per thing the routine can do, with its whole requirement stack in it.
//
// This replaces the two-column panel (conduct docs left, capabilities right, each badged with
// what required it). That layout was faithful to the model and asked the reader to do the join:
// to see whether "reach a person on Discord" actually works you had to find the doc, find the
// capability it required, then leave for the Secrets panel and the Filesystem panel to check the
// rest. Three of the four halves were on other screens.
//
// A conduct doc already IS an ability — prose plus the capabilities it presumes — so the card is
// the doc, and everything the ability needs hangs under it: the capabilities from `requires:`,
// and (when a surface is supplied) the secrets, private stores and bindings the resolver derived
// from the util headers. Rows are attributed by the surface's machine-readable `source`, never by
// reading its prose.
//
// The ADDRESSES this file owns. Each is a landing site another module aims at, so renaming one
// is an edit to that module and not a local rename; each survives a retitling, a reordering and
// a repaint, so keep it on whatever element holds the control it names.
//
//   [data-ability="<slug>"]       an ability card or catalogue row. The surface diagnoses a doc
//                                 whose requirements are not all switched on and lands HERE
//                                 (`switch_on`), because the dial that closes it is inside the
//                                 card, where a jump to the panel flashes every ability at once
//                                 (components/surface-view.js). An `install_util` row for a util
//                                 a held doc still REQUIRES lands here too: only a run writes a
//                                 util, so unticking the doc is the whole of a person's half;
//                                 the card's own row for that util carries the absence.
//   [data-ability="(uncovered)"]  the card holding every capability no held doc requires.
//   [data-orphan="<class:name>"]  one such capability's row. On EVERY row of that card, the
//                                 ones this panel cannot act on included, so a reader who
//                                 arrives lands on a sentence saying what settles it instead.
//   [data-drop="<class:name>"]    the button that settles that capability here — where the
//                                 surface's `cover_or_drop` and `install_util` offers aim. It
//                                 reads "drop" while the capability is on, "keep" once a drop
//                                 is staged, which is the same control answering the same
//                                 question. Present ONLY where pressing it does something
//                                 real: a capability this routine owns, that no held doc puts
//                                 straight back. An offer landing on a control that cannot
//                                 perform it is the defect this attribute exists to make
//                                 impossible, so it is on the button, never on the row.
//
// Below the cards sit the four SETTINGS no doc switches on — run history, the reminder layer, the
// shared-reminder approval and the task layer — on a card of their own
// (components/abilities-settings.js).
//
// (permissions, capabilities, opts) in, {node, value} out, for its three hosts: the routine page,
// the conversation rail and the composer.
// `opts.surface` is optional — an unsaved conversation has no routine to resolve, so the cards
// degrade to the two-layer view that case can support.
//
// Two ways to commit. With `opts.onSave` the panel carries its own save button (a conversation's
// header). With `opts.onChange` it carries none: every staged change is reported as `value()` the
// moment it is made, to a caller that keeps a DRAFT (the routine page's settings form, saved by the
// page's one accept). There `opts.saved` names what the routine HOLDS — `{permissions, capabilities}`
// — which is what a staged row is marked against, while `permissions`/`capabilities` are the draft.

import { effectLine } from "/static/components/effectline.js";
import { el } from "/static/util.js";
import { docExpander } from "/static/components/docexpand.js";

import {
  CONFIRM_OPTIONS, RULE_CONFIRM_OPTIONS, SETTING_DEFAULTS, ACTION_HELP, UTIL_HELP, ABSENT_UTIL,
  KIND_LABEL
} from "/static/components/abilities-data.js";
import { createOrphanCard } from "/static/components/abilities-orphans.js";
import { selectDial, settingsCard } from "/static/components/abilities-settings.js";

export function abilitiesPanel(permissions, capabilities, opts = {}) {
  const docs = permissions || [];
  const held = new Set(docs.filter((p) => p.active && !p.routine_only).map((p) => p.slug));
  // The settings half of the mapping rests on what the panel is GIVEN: every read fills all five
  // (the detail through routines_common.permission_layers_detail, the routine page's draft
  // through patterns/fields.effective_capabilities). Only a draft can leave one out — a proposal
  // naming just actions and utils — and that one shows what saving it would write.
  const caps = {
    actions: new Set(capabilities?.active?.actions || []),
    utils: new Set(capabilities?.active?.utils || []),
    ...Object.fromEntries(Object.entries(SETTING_DEFAULTS)
      .map(([key, fallback]) => [key, capabilities?.active?.[key] || fallback])),
  };
  const surface = opts.surface?.nodes || null;
  // The mapping as SAVED. The panel is rebuilt from a fresh read after every save, so this is
  // the committed truth each time, while the surface beside it is the snapshot the page loaded
  // with — one in which a capability the last save dropped still has a row. That row is
  // dropped rather than offered again; a row whose capability is still saved is staged.
  const savedActive = opts.saved?.capabilities || capabilities?.active || {};
  const savedCaps = opts.saved
    ? { util: new Set(savedActive.utils || []), action: new Set(savedActive.actions || []) }
    : { util: new Set(caps.utils), action: new Set(caps.actions) };
  // what the sections are built from: the SAVED set, so a doc the draft switches on reads as a
  // staged row in the catalogue until it is accepted, exactly as an unsaved tick does
  const committed = new Set(opts.saved ? opts.saved.permissions || [] : held);
  const marks = [];                         // [{slug, node}] — repainted on every toggle
  let ready = false;                        // the first render reports nothing: building is not editing

  // A doc's `requires:` names ACTIONS and UTILS and nothing else (grants.normalize_capabilities
  // drops any other key: the approval dials, the run-history depth and the reminder stores are
  // settings the user chooses, never something holding a doc switches on).
  const needs = (p) => p.requires || {};
  const baseName = (u) => String(u).split(":")[0];

  // The activation cascade: ticking a doc on switches on what it requires. Nothing cascades
  // the other way, because nothing here can switch a REQUIRED capability off: a doc is the
  // switch for what it needs, and the one place a capability comes off — the uncovered card —
  // offers only those no held doc requires. A "deactivation cascade" once ran after every
  // untick, which changes no capability, so all it did was untick every OTHER held doc that was
  // already short of a requirement (one a library change gave it — its card's "switch on" is
  // the remedy) and toast "also switched off" as if the untick had done it.
  const raiseFor = (r) => {
    (r.actions || []).forEach((a) => caps.actions.add(a));
    (r.utils || []).forEach((u) => caps.utils.add(u));
  };

  /** Surface rows this ability owns: by declaring doc, or by a util it reserves. */
  function resourceRows(doc) {
    if (!surface) return [];
    const mine = new Set((needs(doc).utils || []).map(baseName));
    return surface.filter((n) => {
      const src = n.source || {};
      if (src.doc) return src.doc === doc.slug;
      return (src.utils || []).some((u) => mine.has(u));
    });
  }

  /** Reserved utils the LIBRARY does not have, by name. The surface reports each as a `util:`
   *  row in state "absent"; one a held doc still requires is deliberately not in the uncovered
   *  card, because dropping it there is undone by the raise. So the card of the doc that
   *  requires it is where that diagnosis is answered; this is what marks the row there. */
  const absentUtils = new Set((surface || [])
    .filter((n) => n.state === "absent" && n.id.startsWith("util:"))
    .map((n) => baseName(n.id.slice("util:".length))));

  const host = el("div", { class: "ability-panel" });
  // Built once and re-appended by every render, so the card inside it can be rebuilt on a
  // staged change without touching anything else on screen.
  const orphanSlot = el("div", { class: "mt" });

  function stackRow({ state, kind, entity, note, control }) {
    return el("li", { class: `ab-row st-${state}${control ? " has-control" : ""}`,
                      "data-entity": entity },
      el("span", { class: "dot" }),
      el("span", { class: "kind" }, kind),
      el("div", { class: "ent" },
        control || el("span", { class: "ent-id" }, entity),
        note ? el("div", { class: "muted small prose" }, note) : null));
  }

  /** The approval dial a doc's requirement rides on — write_util's `confirm`, write_rule's
   *  `rule_confirm` — or null. Its row is never short: an approval level is the user's policy,
   *  and a `requires:` names only actions and utils, so there is nothing for the value to fall
   *  below.
   */
  function dialFor(doc) {
    const r = needs(doc);
    const dial = (options, key, label) => ({ kind: "approval", state: "ok",
      control: selectDial(options, caps[key], (v) => { caps[key] = v; },
                          { key, label, after: render }) });
    if ((r.actions || []).includes("write_util")) {
      return dial(CONFIRM_OPTIONS, "confirm", "who approves a util change");
    }
    if ((r.actions || []).includes("write_rule")) {
      return dial(RULE_CONFIRM_OPTIONS, "rule_confirm", "who approves a rule change");
    }
    return null;
  }

  /** A compact catalogue row for an ability the routine does NOT hold. No stack, no state:
   *  nothing is outstanding for something the routine is not doing, and painting its
   *  requirements red said the opposite. */
  function availableRow(doc) {
    const box = el("input", { type: "checkbox",
                              disabled: doc.routine_only ? "" : null });
    box.onchange = () => {
      if (box.checked) { held.add(doc.slug); raiseFor(needs(doc)); }
      else held.delete(doc.slug);
      repaint();
    };
    const node = el("label", { class: "avail-row", "data-ability": doc.slug,
                         title: doc.routine_only ? "only meaningful for scheduled routines" : "" },
      box,
      el("span", { class: "avail-name" }, doc.slug),
      effectLine(doc, false));
    marks.push({ slug: doc.slug, node, box });
    return node;
  }

  function card(doc) {
    const r = needs(doc);
    const rows = [];
    for (const a of r.actions || []) {
      rows.push({ state: caps.actions.has(a) ? "ok" : "blocks", kind: "action", entity: a,
                  note: ACTION_HELP[a] || "" });
    }
    for (const u of r.utils || []) {
      const absent = absentUtils.has(baseName(u));
      rows.push({ state: absent || !caps.utils.has(u) ? "blocks" : "ok", kind: "util", entity: u,
                  note: absent ? ABSENT_UTIL : UTIL_HELP[baseName(u)] || "" });
    }
    const derived = resourceRows(doc);   // a card is built only for what the routine holds
    for (const n of derived) {
      const [cls, ...rest] = n.id.split(":");
      rows.push({ state: n.severity, kind: KIND_LABEL[cls] || cls,
                  entity: rest.join(":") || n.id, note: n.effect || n.why });
    }
    const dial = dialFor(doc);
    if (dial) {
      rows.push({ state: dial.state, kind: dial.kind, entity: "policy", control: dial.control });
    }

    const box = el("input", { type: "checkbox", checked: "",
                              disabled: doc.routine_only ? "" : null });
    box.onchange = () => {
      if (box.checked) { held.add(doc.slug); raiseFor(r); }
      else held.delete(doc.slug);
      repaint();
    };
    // the card's own verdict: the worst state in its stack, which is the whole reason the
    // stack lives inside the card rather than across three other panels
    const bad = rows.some((r2) => r2.state === "blocks") ? "blocks"
      : rows.some((r2) => r2.state === "interrupts") ? "interrupts" : "";
    // the badge's modifier is the SAME surface word as the card's class (views.css colours the
    // pill from that class); an err/warn pair here was a second vocabulary nothing read
    const badge = el("span", { class: `pill ${bad || "ok"}` },
      bad === "blocks" ? "will fail" : bad === "interrupts" ? "needs a decision" : "ready");
    const doc_ = docExpander("permissions", doc.slug);
    // A HELD doc whose requirements are not all switched on fails closed — and the switch that
    // closes it is here, in the card that names the gap: one press raises the mapping to cover
    // what this doc requires (a util the library does not have stays out of reach; only a run
    // writes one). It is the landing site of the surface's `switch_on` offer.
    const missing = [...(r.actions || []).filter((a) => !caps.actions.has(a)),
      ...(r.utils || []).filter((u) => !caps.utils.has(u) && !absentUtils.has(baseName(u)))];
    const switchOn = missing.length
      ? el("button", { type: "button", class: "btn small", "data-switch-on": doc.slug,
          title: `switch on what ${doc.slug} requires — ${missing.join(", ")}`,
          onclick: () => { raiseFor(r); render(); } }, `switch on ${missing.join(", ")}`)
      : null;
    const node = el("div", { class: `ability${bad ? ` ${bad}` : ""}`,
                             "data-ability": doc.slug },
      el("label", { class: "ability-head",
                    title: doc.routine_only ? "only meaningful for scheduled routines" : "" },
        box,
        el("div", {},
          el("div", { class: "ability-name" }, doc.slug,
             doc.routine_only ? " (routines only)" : ""),
          effectLine(doc, true)),
        badge),
      // A READY card's stack is confirmation of what its badge already said, and eleven of them
      // in a three-column grid is a screen and a half of settled requirements. It folds — and
      // ONLY when nothing is outstanding: a card that will fail or needs a decision keeps every
      // row in front of the reader, which is what the card view was built for. A fix link from
      // the setup check still lands inside, because jumpToSection opens every <details> on the
      // way down.
      !rows.length ? null
        : bad ? el("ul", { class: "ability-stack" }, ...rows.map(stackRow))
        : el("details", { class: "ability-more" },
            el("summary", {}, `${rows.length} requirement${rows.length === 1 ? "" : "s"} · all met`),
            el("ul", { class: "ability-stack" }, ...rows.map(stackRow))),
      el("div", { class: "ability-foot row", style: "gap:10px" }, switchOn, doc_.btn), doc_.body);
    marks.push({ slug: doc.slug, node, box });
    return node;
  }

  const orphanCard = createOrphanCard({
    surface, caps, savedCaps, held, docs, needs, baseName, stackRow, render });
  // Built once and re-appended by every render, like the orphan slot: its values move only
  // through its own dials, and a change there reports through repaint() without a rebuild.
  const settings = [el("div", { class: "lbl" }, "Settings"),
                    settingsCard({ caps, stackRow, changed: () => repaint() })];

  /** Which rows are staged for a change, whether save is live — and the uncovered card, which
   *  is rebuilt rather than repainted. What a row there OFFERS is decided by the docs ticked on
   *  above it, so a stale card is one offering a drop that ticking a doc has already settled:
   *  the reader would press a control the save then reverses. Every staged change lands here,
   *  which is what makes that impossible. */
  function repaint() {
    for (const { slug, node, box } of marks) {
      const staged = held.has(slug) !== committed.has(slug);
      node.classList.toggle("pending", staged);
      node.classList.toggle("pending-drop", staged && committed.has(slug));
      if (box) box.checked = held.has(slug);
    }
    orphanSlot.replaceChildren(...[orphanCard()].filter(Boolean));
    if (ready) opts.onChange?.(value());
  }

  function render() {
    marks.length = 0;
    const on = docs.filter((p) => (p.routine_only ? p.active : committed.has(p.slug)));
    const off = docs.filter((p) => !on.includes(p));
    host.replaceChildren();
    if (!docs.length) {
      // the settings are the routine's whatever the library holds
      host.append(el("div", { class: "muted" }, "no permissions in the library"), ...settings);
      return;
    }
    host.append(el("div", { class: "lbl" }, `Holds · ${on.length}`));
    if (on.length) {
      host.append(el("div", { class: "abilities" }, ...on.map(card)));
    } else {
      host.append(el("div", { class: "set-desc muted small" },
        "this routine holds no conduct permissions — it can read, write in its own dir and "
        + "call ungated utils, nothing more"));
    }
    // Outside the ability grid, because the uncovered card belongs to the reader whether or not
    // this routine holds a single doc — and a routine holding NONE is where a capability
    // switched on by nothing is likeliest. Inside the grid it had nowhere to appear in that
    // case, which is the one case the card is most about.
    host.append(orphanSlot, ...settings);
    if (off.length) {
      const avail = [
        el("div", { class: "set-desc muted small", style: "margin:-4px 0 8px" },
          "switching one on switches on the capabilities it needs; it moves up once saved"),
        el("div", { class: "avail" }, ...off.map(availableRow)),
      ];
      // Fourteen cards of what this routine does NOT hold, each with its own ON/OFF/WHEN lines,
      // is ~5 000px on a phone — between the held set and the save button. Every line is kept;
      // on a narrow screen the list is a disclosure. Wide, it stays open: there the held set and
      // the catalogue are read together, and a setup-check fix link lands straight on a card.
      host.append(window.matchMedia("(min-width: 861px)").matches
        ? el("div", {}, el("div", { class: "lbl mt" }, `Available · ${off.length}`), ...avail)
        : el("details", { class: "mt avail-fold" },
            el("summary", { class: "lbl" }, `Available · ${off.length}`), ...avail));
    }
    repaint();
  }
  render();
  ready = true;

  function value() {
    return {
      active: docs.filter((p) => (p.routine_only ? p.active : held.has(p.slug))).map((p) => p.slug),
      capabilities: { actions: [...caps.actions], utils: [...caps.utils],
                      ...Object.fromEntries(Object.keys(SETTING_DEFAULTS).map((k) => [k, caps[k]])) },
    };
  }

  let footer = null;
  if (opts.onSave) {
    const saveBtn = el("button", { class: "btn primary" }, "save permissions");
    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      try { await opts.onSave(value()); } finally { saveBtn.disabled = false; }
    };
    footer = el("div", { class: "row mt" }, saveBtn);
  }
  return { node: el("div", {}, host, footer), value };
}