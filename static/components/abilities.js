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
//                                 arrives lands on a sentence saying where the act does live.
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
// Drop-in for permissionsPanel: same (permissions, capabilities, opts) in, same {node, value} out,
// so the routine page, the conversation rail, the composer and the domain editor all keep working.
// `opts.surface` is optional — a domain's shared config and an unsaved conversation have no
// routine to resolve, and the cards degrade to the two-layer view those cases can support.

import { effectLine } from "/static/components/effectline.js";
import { el, toast } from "/static/util.js";
import { docExpander } from "/static/components/docexpand.js";

const CONFIRM_OPTIONS = [
  ["off", "off — engine rejects write_util"],
  ["always", "on — every create/revise asks you"],
  ["creations", "on — new utils ask; revisions are autonomous"],
  ["never", "on — fully autonomous (selftest-gated)"],
];
const RULE_CONFIRM_OPTIONS = [
  ["off", "off — engine rejects write_rule"],
  ["always", "on — every rule change asks you"],
  ["creations", "on — new rules ask; revisions are autonomous"],
  ["never", "on — fully autonomous (lint-gated)"],
];
const RUNS_OPTIONS = [
  ["none", "off — previous runs unreadable"],
  ["last", "the last run only"],
  ["all", "all previous runs"],
];
const RUNS_RANK = { none: 0, last: 1, all: 2 };
const WF_OPTIONS = [
  ["catalog", "catalog — pick existing patterns only"],
  ["generate", "generate — also draft a new pattern when none fits"],
];
const WF_RANK = { catalog: 0, generate: 1 };
// ONE control for the reminder layer, because the two dials behind it are one decision: which
// stores the routine reads, and — only once it reaches the shared one — who approves a write
// there. A `local` routine has nothing to approve: its own store is autonomous by design.
const REMINDERS_OPTIONS = [
  ["off", "off — nothing is stored, nothing is held"],
  ["local", "its own store — cautions it wrote for itself"],
  ["global:always", "+ the shared store — every shared change asks you"],
  ["global:creations", "+ the shared store — new ones ask; edits are autonomous"],
  ["global:never", "+ the shared store — fully autonomous"],
];
const REM_RANK = { none: 0, local: 1, global: 2 };

// What a gated capability MEANS, with a concrete example — a bare action kind told the reader
// nothing (F178, user order 2026-07-23). Kept verbatim from the panel this replaces.
const ACTION_HELP = {
  write_util: "create or revise the shared global utils every routine can call — e.g. write a "
    + "`pdf-stamp` script once and every routine can sign PDFs from then on",
  remove_util: "retire a global util from the shared library (refused while another util still "
    + "calls it) — e.g. delete a scraper after its site shut down",
  revise_util: "change an existing global util in place, without being able to create new ones",
  memory_read: "read the routine's .memory/ notebook — facts earlier runs paid to learn, "
    + "e.g. \"the API rejects requests without a language header\"",
  memory_write: "add or revise .memory/ notes when reality contradicts an assumption",
  detach: "start a long background job that outlives the current reply — e.g. kick off a "
    + "two-hour bulk conversion, keep chatting, and the result is delivered back when it finishes",
  schedule_run: "arm a one-shot future run of this or a sibling routine — e.g. \"re-check the "
    + "parcel status in 3 days\" instead of waiting for the next scheduled fire",
  write_rule: "author or revise a general rule in the shared library — the text every routine "
    + "holding that rule follows from its next run",
  script: "run the routine's own persistent scripts/<name>.py helpers",
  shell: "run one arbitrary command on the host — the escape hatch around the util library, "
    + "e.g. a quick `git log` no util covers. It runs in the same sandbox a util does; "
    + "anything the routine does twice should become a proper util instead",
  write_recipe: "rewrite this routine's OWN instructions — main.md, stages/ and tuning.yaml. "
    + "Its config (routine.yaml) stays sealed either way. Hold it where refining the recipe "
    + "IS the job; an ordinary routine reports a wrong instruction instead of rewording it",
};
const UTIL_HELP = {
  discord: "the phone channel: blocking questions mirror to Discord and are answerable in one "
    + "reply — e.g. \"apply to this project? approve / decline\" reaches you away from the console",
  remote: "act on a bound remote machine over SSH — e.g. fetch a file from the NAS or restart "
    + "a service on another box",
};
// A reserved util a held doc requires that the library does not have. The mapping says on, so
// the row would otherwise read as satisfied while every call fails — this is the line the
// surface's `install_util` diagnosis lands on, carrying the sentence that closes it.
const ABSENT_UTIL = "no util by that name is in the library — every call fails. Only a run "
  + "writes a util, so the act here is unticking this ability.";
const KIND_LABEL = { "secret": "secret", "fs-write": "write root", "fs-read": "read root",
                     "machine": "machine", "connection": "connection", "util": "util",
                     "action": "action", "permission": "conduct" };

export function abilitiesPanel(permissions, capabilities, opts = {}) {
  const docs = permissions || [];
  const held = new Set(docs.filter((p) => p.active && !p.routine_only).map((p) => p.slug));
  const caps = {
    actions: new Set(capabilities?.active?.actions || []),
    utils: new Set(capabilities?.active?.utils || []),
    confirm: capabilities?.active?.confirm || "always",
    rule_confirm: capabilities?.active?.rule_confirm || "always",
    runs: capabilities?.active?.runs || "none",
    workflows: capabilities?.active?.workflows || "catalog",
    reminders: capabilities?.active?.reminders || "none",
    remind_confirm: capabilities?.active?.remind_confirm || "always",
  };
  const surface = opts.surface?.nodes || null;
  // The mapping as SAVED. The panel is rebuilt from a fresh read after every save, so this is
  // the committed truth each time, while the surface beside it is the snapshot the page loaded
  // with — one in which a capability the last save dropped still has a row. That row is
  // dropped rather than offered again; a row whose capability is still saved is staged.
  const savedCaps = { util: new Set(caps.utils), action: new Set(caps.actions) };
  const committed = new Set(held);          // what the sections are built from
  const marks = [];                         // [{slug, node}] — repainted on every toggle

  const needs = (p) => p.requires || {};
  const baseName = (u) => String(u).split(":")[0];

  // The three RANKED dials in one table — the `requires:` key each answers, against the ladder
  // it is ranked on. A doc names a FLOOR, so a live value at or above it satisfies the doc; a
  // value the ladder does not know is not one this can call satisfied. Every reading of a dial
  // goes through it: the raise, the deactivation cascade and the state the card's dial row
  // wears. The server builds the `switch_on` fix's `missing` list by raising the mapping through
  // its own cascade and reporting every key the raise CHANGED, which is this comparison — so the
  // dot the reader sees and the shortfall the fix names can only agree.
  const RANKED = { runs: RUNS_RANK, workflows: WF_RANK, reminders: REM_RANK };
  const dialMet = (key, need) => !need || RANKED[key][caps[key]] >= RANKED[key][need];
  // the activation cascade: raise the mapping to cover one doc's requires
  const raiseFor = (r) => {
    (r.actions || []).forEach((a) => caps.actions.add(a));
    (r.utils || []).forEach((u) => caps.utils.add(u));
    for (const key of Object.keys(RANKED)) if (!dialMet(key, r[key])) caps[key] = r[key];
  };
  // the deactivation cascade: drop what the mapping no longer covers
  const dropUnsatisfied = () => {
    const dropped = [];
    for (const slug of [...held]) {
      const r = needs(docs.find((d) => d.slug === slug) || {});
      const ok = (r.actions || []).every((a) => caps.actions.has(a))
        && (r.utils || []).every((u) => caps.utils.has(u))
        && Object.keys(RANKED).every((key) => dialMet(key, r[key]));
      if (!ok) { held.delete(slug); dropped.push(slug); }
    }
    if (dropped.length) toast(`also switched off: ${dropped.join(", ")}`);
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

  /** The one dial a doc's requirement rides on, with the STATE its row wears.
   *
   *  An approval dial is never short: an approval level is the user's policy and a `requires:`
   *  may not name one (grants.normalize_capabilities rejects it inside requires), so there is
   *  nothing for the value to fall below. A ranked dial is short whenever the live value sits
   *  under what the doc asks for — the row the reader has to close, wearing the same `blocks`
   *  the card's badge is built from and naming the same shortfall the server put in the fix.
   */
  function dialFor(doc) {
    const r = needs(doc);
    if ((r.actions || []).includes("write_util")) {
      return { kind: "approval", state: "ok",
               control: selectDial(CONFIRM_OPTIONS, caps.confirm, (v) => { caps.confirm = v; }) };
    }
    if ((r.actions || []).includes("write_rule")) {
      return { kind: "approval", state: "ok",
               control: selectDial(RULE_CONFIRM_OPTIONS, caps.rule_confirm,
                                   (v) => { caps.rule_confirm = v; }) };
    }
    const state = (key) => (dialMet(key, r[key]) ? "ok" : "blocks");
    if (r.runs) {
      return { kind: "depth", state: state("runs"),
               control: selectDial(RUNS_OPTIONS, caps.runs, (v) => { caps.runs = v; },
                                   opts.disableRuns) };
    }
    if (r.workflows) {
      return { kind: "sourcing", state: state("workflows"),
               control: selectDial(WF_OPTIONS, caps.workflows,
                                   (v) => { caps.workflows = v; }) };
    }
    if (r.reminders) {
      // One control over two keys, so its value is a pair. `none` is a real place for the
      // mapping to stand — a file edited by hand holds this doc with the layer switched off —
      // and it is shown as such rather than displayed as the `local` it is not.
      const now = caps.reminders === "none" ? "off"
        : caps.reminders === "global" ? `global:${caps.remind_confirm}` : "local";
      return { kind: "stores", state: state("reminders"),
               control: selectDial(REMINDERS_OPTIONS, now, (v) => {
                 const [level, confirm] = v.split(":");
                 caps.reminders = level === "off" ? "none" : level;
                 if (confirm) caps.remind_confirm = confirm;
               }) };
    }
    return null;
  }

  function selectDial(options, current, set, disabled) {
    // `disabled` is the caller's SENTENCE saying why this dial cannot move (a conversation is
    // one continuous run, so previous-run depth means nothing there). A greyed control with no
    // explanation is a dead end, so the sentence rides the control as its tooltip.
    //
    // A card whose doc requires the capability never OFFERS the off value — off is the engine
    // rejecting the very thing the ability is for. It still SHOWS it while that is where the
    // mapping actually stands: a control resting on a value the routine does not hold reads as
    // a closed row, leaving the reader nothing to move.
    const sel = el("select", { disabled: disabled ? "" : null, title: disabled || null },
      ...options.filter(([v]) => v !== "off" || v === current).map(([v, label]) =>
        el("option", { value: v, selected: current === v ? "" : null }, label)));
    sel.onchange = () => { set(sel.value); render(); };
    return sel;
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
    for (const t of r.util_tags || []) {
      rows.push({ state: "ok", kind: "util class", entity: t });
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
      else { held.delete(doc.slug); dropUnsatisfied(); }
      repaint();
    };
    // the card's own verdict: the worst state in its stack, which is the whole reason the
    // stack lives inside the card rather than across three other panels
    const bad = rows.some((r2) => r2.state === "blocks") ? "blocks"
      : rows.some((r2) => r2.state === "interrupts") ? "interrupts" : "";
    const badge = bad === "blocks" ? el("span", { class: "pill err" }, "will fail")
      : bad === "interrupts" ? el("span", { class: "pill warn" }, "needs a decision")
      : el("span", { class: "pill ok" }, "ready");
    const doc_ = docExpander("permissions", doc.slug);
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
      rows.length ? el("ul", { class: "ability-stack" }, ...rows.map(stackRow)) : null,
      el("div", { class: "ability-foot" }, doc_.btn), doc_.body);
    marks.push({ slug: doc.slug, node, box });
    return node;
  }

  /** The live mapping an entity class comes off. `util:` and `action:` are the two classes a
   *  capability row carries — the two this panel switches; anything else the surface reports is
   *  somebody else's to change. */
  const capSet = (cls) => (cls === "util" ? caps.utils : cls === "action" ? caps.actions : null);

  /** Would a doc the panel holds RIGHT NOW put this capability straight back? The server raises
   *  the mapping to cover every held doc's `requires:` before it floors it, so dropping what a
   *  held doc asks for changes nothing. The surface answered this against the SAVED docs; asked
   *  again here it catches a covering doc ticked on since — the row stops offering a drop the
   *  moment the checkbox above became the better way out. */
  function coveredNow(cls, name) {
    return [...held].some((slug) => {
      const r = needs(docs.find((d) => d.slug === slug) || {});
      return cls === "action" ? (r.actions || []).includes(name)
        : (r.utils || []).some((u) => baseName(u) === baseName(name));
    });
  }

  /** One capability row of the orphan card, read once: which mapping owns it, who switched it
   *  on, and where it stands between the saved mapping and the staged one. */
  function orphanInfo(node) {
    const [cls, ...rest] = node.id.split(":");
    const name = rest.join(":");
    const fix = node.fix || {};
    const set = capSet(cls);
    const saved = savedCaps[cls] || new Set();
    return { node, cls, name, set,
             // Provenance rides the fix, the one place it is a fact rather than a guess: a
             // capability the DOMAIN's shared block names survives this routine's save (the
             // floor counts inherited permissions, because that one is the domain's to drop).
             // `fix.domain` alone: an install_util fix also carries `name`, which is the
             // UTIL, so preferring it would name the util as the domain that supplied it.
             domain: fix.owner === "domain" ? (fix.domain || "") : "",
             covered: coveredNow(cls, name),
             gone: !saved.has(name),
             staged: saved.has(name) && !!set && !set.has(name) };
  }

  /** What one row hands the reader, which depends entirely on whose capability it is.
   *
   *  OWN: the drop — staged like every other change here, committed by the same save, and the
   *  landing site the surface's `cover_or_drop` / `install_util` offers aim at.
   *  DOMAIN: nothing to press. A drop here is a no-op the next load undoes, so the row names
   *  the domain that switched it on and travels to the editor that owns it.
   *  COVERED SINCE: nothing to press either — saving now settles the row the other way.
   */
  function orphanOffer(info) {
    const { node, name } = info;
    const why = node.state === "uncovered" ? node.why
      : [node.why, node.effect].filter(Boolean).join(" — ");
    if (info.covered) {
      return { note: `${why}. A doc ticked on above requires it, so saving covers this row `
                 + "instead: the capability stays, with the conduct prose behind it." };
    }
    const id = el("span", { class: "ent-id" }, name);
    if (info.domain) {
      return { control: el("div", { class: "row" }, id,
                 el("a", { class: "btn small ghost", href: "#/routines",
                           title: `the domain “${info.domain}” switched this on; its shared `
                             + "config is edited on the Routines page" },
                   "the domain’s config ↗")),
               note: `${why}. A capability the domain supplies survives a save here, so it `
                 + "comes off in that domain's shared config. Ticking on a doc above that "
                 + "requires it settles the row in this panel instead." };
    }
    if (info.staged) {
      return { control: el("div", { class: "row" }, el("s", { class: "ent-id" }, name),
                 el("button", { type: "button", class: "btn small ghost", "data-drop": node.id,
                                title: `put ${name} back — nothing is saved yet`,
                                onclick: () => { info.set.add(name); render(); } }, "keep")),
               note: "staged: the save below switches it off, after which the routine cannot "
                 + "do this at all." };
    }
    return { control: el("div", { class: "row" }, id,
               el("button", { type: "button", class: "btn small ghost", "data-drop": node.id,
                              title: `switch ${name} off — the routine loses it entirely. `
                                + "Ticking on a doc above that requires it is the other way to "
                                + "settle this row.",
                              onclick: () => { info.set.delete(name); render(); } }, "drop")),
             note: why };
  }

  /** Capabilities switched on that no held doc requires — the domain-inheritance blind spot
   *  and the one place a capability comes off. Dropping one was reachable only as a side effect
   *  of pressing save: the server floors away whatever no held doc requires, so the routine lost
   *  it with nothing on screen saying so.
   *
   *  The reserved util MISSING from the library is here too, because it is the same act. Only a
   *  run writes a util, so the half a person performs is dropping the name — out of the mapping
   *  this card edits. One that a held doc still requires is not in this card: dropping it would
   *  be undone by the raise — the doc holding it is the thing to untick.
   */
  function orphanCard() {
    if (!surface) return null;
    const rows = surface.filter((n) => n.state === "uncovered" || n.state === "absent")
      .map(orphanInfo)
      // `gone` is a capability the last save already settled, or one held through a util TAG,
      // which this card does not switch. Either way the mapping in front of the reader no
      // longer holds it, so there is nothing here to act on.
      .filter((i) => i.set && !i.gone && !(i.node.state === "absent" && i.covered));
    if (!rows.length) return null;
    return el("div", { class: "ability orphan", "data-ability": "(uncovered)" },
      el("div", { class: "ability-head" },
        el("span", {}, "⚑"),
        el("div", {},
          el("div", { class: "ability-name" }, "Switched on by nothing here"),
          el("div", { class: "muted small prose" },
            "The routine may use these, but no conduct doc above asked for them — so it acts "
            + "without the prose that normally comes with them. Two ways out of a row: tick on "
            + "a doc above that requires it, which brings that prose with it, or drop the "
            + "capability, after which the routine can no longer do the thing at all.")),
        el("span", { class: "pill soft" }, `${rows.length}`)),
      el("ul", { class: "ability-stack" }, ...rows.map((info) => {
        const li = stackRow({ state: info.node.severity, entity: info.name,
                              kind: KIND_LABEL[info.cls] || info.cls, ...orphanOffer(info) });
        li.setAttribute("data-orphan", info.node.id);
        return li;
      })));
  }

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
  }

  function render() {
    marks.length = 0;
    const on = docs.filter((p) => (p.routine_only ? p.active : committed.has(p.slug)));
    const off = docs.filter((p) => !on.includes(p));
    host.replaceChildren();
    if (!docs.length) {
      host.append(el("div", { class: "muted" }, "no permissions in the library"));
      return;
    }
    host.append(el("div", { class: "lbl" }, `Holds · ${on.length}`));
    if (on.length) {
      host.append(el("div", { class: "abilities" }, ...on.map(card)));
    } else {
      host.append(el("div", { class: "muted small" },
        "this routine holds no conduct permissions — it can read, write in its own dir and "
        + "call ungated utils, nothing more"));
    }
    // Outside the ability grid, because the uncovered card belongs to the reader whether or not
    // this routine holds a single doc — and a routine holding NONE is where a capability
    // switched on by nothing is likeliest. Inside the grid it had nowhere to appear in that
    // case, which is the one case the card is most about.
    host.append(orphanSlot);
    if (off.length) {
      host.append(el("div", { class: "lbl mt" }, `Available · ${off.length}`),
        el("div", { class: "muted small", style: "margin:-4px 0 8px" },
          "switching one on switches on the capabilities it needs; it moves up once saved"),
        el("div", { class: "avail" }, ...off.map(availableRow)));
    }
    repaint();
  }
  render();

  const value = () => ({
    active: docs.filter((p) => (p.routine_only ? p.active : held.has(p.slug))).map((p) => p.slug),
    capabilities: { actions: [...caps.actions], utils: [...caps.utils],
                    util_tags: capabilities?.active?.util_tags || [],
                    confirm: caps.confirm, rule_confirm: caps.rule_confirm,
                    remind_confirm: caps.remind_confirm,
                    runs: caps.runs, workflows: caps.workflows,
                    reminders: caps.reminders },
  });

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