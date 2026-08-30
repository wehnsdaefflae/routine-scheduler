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
// Drop-in for permissionsPanel: same (permissions, capabilities, opts) in, same {node, value} out,
// so the routine page, the conversation rail, the composer and the group editor all keep working.
// `opts.surface` is optional — a group's shared config and an unsaved conversation have no
// routine to resolve, and the cards degrade to the two-layer view those cases can support.

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
  write_recipe: "rewrite this routine's OWN instructions — main.md, stages/ and tuning.yaml. "
    + "Its config (routine.yaml) stays sealed either way. Hold it where refining the recipe "
    + "IS the job; an ordinary routine reports a wrong instruction instead of rewording it",
};
const UTIL_HELP = {
  discord: "the phone channel: blocking questions mirror to Discord and are answerable in one "
    + "reply — e.g. \"apply to this project? approve / decline\" reaches you away from the console",
  shell: "arbitrary one-off shell commands on the host — the escape hatch around the no-shell "
    + "design, e.g. a quick `git log` no util covers; anything a routine does twice should "
    + "become a proper util instead",
  remote: "act on a bound remote machine over SSH — e.g. fetch a file from the NAS or restart "
    + "a service on another box",
};
const KIND_LABEL = { "secret": "secret", "fs-write": "write root", "fs-read": "read root",
                     "machine": "machine", "connection": "connection", "util": "util",
                     "action": "action", "permission": "conduct" };

/** The severity of the worst row in a list, or "" when they are all satisfied. */
function worst(rows) {
  if (rows.some((r) => r.severity === "blocks")) return "blocks";
  if (rows.some((r) => r.severity === "interrupts")) return "interrupts";
  return "";
}

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
  };
  const surface = opts.surface?.nodes || null;
  const committed = new Set(held);          // what the sections are built from
  const marks = [];                         // [{slug, node}] — repainted on every toggle

  const needs = (p) => p.requires || {};
  // the activation cascade: raise the mapping to cover one doc's requires
  const raiseFor = (r) => {
    (r.actions || []).forEach((a) => caps.actions.add(a));
    (r.utils || []).forEach((u) => caps.utils.add(u));
    if (r.runs && RUNS_RANK[caps.runs] < RUNS_RANK[r.runs]) caps.runs = r.runs;
    if (r.workflows && WF_RANK[caps.workflows] < WF_RANK[r.workflows]) caps.workflows = r.workflows;
  };
  // the deactivation cascade: drop what the mapping no longer covers
  const dropUnsatisfied = () => {
    const dropped = [];
    for (const slug of [...held]) {
      const r = needs(docs.find((d) => d.slug === slug) || {});
      const ok = (r.actions || []).every((a) => caps.actions.has(a))
        && (r.utils || []).every((u) => caps.utils.has(u))
        && (!r.runs || RUNS_RANK[caps.runs] >= RUNS_RANK[r.runs])
        && (!r.workflows || WF_RANK[caps.workflows] >= WF_RANK[r.workflows]);
      if (!ok) { held.delete(slug); dropped.push(slug); }
    }
    if (dropped.length) toast(`also switched off: ${dropped.join(", ")}`);
  };

  /** Surface rows this ability owns: by declaring doc, or by a util it reserves. */
  function resourceRows(doc) {
    if (!surface) return [];
    const mine = new Set((needs(doc).utils || []).map((u) => String(u).split(":")[0]));
    return surface.filter((n) => {
      const src = n.source || {};
      if (src.doc) return src.doc === doc.slug;
      return (src.utils || []).some((u) => mine.has(u));
    });
  }

  const host = el("div", { class: "ability-panel" });

  function stackRow({ state, kind, entity, note, control }) {
    return el("li", { class: `ab-row st-${state}${control ? " has-control" : ""}`,
                      "data-entity": entity },
      el("span", { class: "dot" }),
      el("span", { class: "kind" }, kind),
      el("div", { class: "ent" },
        control || el("span", { class: "ent-id" }, entity),
        note ? el("div", { class: "muted small prose" }, note) : null));
  }

  function dialFor(doc) {
    const r = needs(doc);
    if ((r.actions || []).includes("write_util")) {
      return ["approval", selectDial(CONFIRM_OPTIONS, caps.confirm, (v) => { caps.confirm = v; })];
    }
    if ((r.actions || []).includes("write_rule")) {
      return ["approval", selectDial(RULE_CONFIRM_OPTIONS, caps.rule_confirm,
                                     (v) => { caps.rule_confirm = v; })];
    }
    if (r.runs) {
      return ["depth", selectDial(RUNS_OPTIONS, caps.runs, (v) => { caps.runs = v; },
                                  opts.disableRuns)];
    }
    if (r.workflows) {
      return ["sourcing", selectDial(WF_OPTIONS, caps.workflows, (v) => { caps.workflows = v; })];
    }
    return null;
  }

  function selectDial(options, current, set, disabled) {
    const sel = el("select", { disabled: disabled ? "" : null },
      ...options.filter(([v]) => v !== "off").map(([v, label]) =>
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
      el("span", { class: "muted small prose" }, doc.summary || ""));
    marks.push({ slug: doc.slug, node, box });
    return node;
  }

  function card(doc) {
    const r = needs(doc);
    const on = true;      // cards are built only for what the routine holds
    const rows = [];
    for (const a of r.actions || []) {
      rows.push({ state: caps.actions.has(a) ? "ok" : "blocks", kind: "action", entity: a,
                  note: ACTION_HELP[a] || "" });
    }
    for (const u of r.utils || []) {
      rows.push({ state: caps.utils.has(u) ? "ok" : "blocks", kind: "util", entity: u,
                  note: UTIL_HELP[String(u).split(":")[0]] || "" });
    }
    for (const t of r.util_tags || []) {
      rows.push({ state: "ok", kind: "util class", entity: t });
    }
    const derived = on ? resourceRows(doc) : [];
    for (const n of derived) {
      const [cls, ...rest] = n.id.split(":");
      rows.push({ state: n.severity, kind: KIND_LABEL[cls] || cls,
                  entity: rest.join(":") || n.id, note: n.effect || n.why });
    }
    const dial = dialFor(doc);
    if (dial && on) rows.push({ state: "ok", kind: dial[0], entity: "policy", control: dial[1] });

    const box = el("input", { type: "checkbox", checked: on ? "" : null,
                              disabled: doc.routine_only ? "" : null });
    box.onchange = () => {
      if (box.checked) { held.add(doc.slug); raiseFor(r); }
      else { held.delete(doc.slug); dropUnsatisfied(); }
      repaint();
    };
    // the card's own verdict: the worst state in its stack, which is the whole reason the
    // stack lives inside the card rather than across three other panels
    const bad = worst(rows.map((r2) => ({ severity: r2.state })));
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
          el("div", { class: "muted small prose" }, doc.summary || "")),
        badge),
      rows.length ? el("ul", { class: "ability-stack" }, ...rows.map(stackRow)) : null,
      el("div", { class: "ability-foot" }, doc_.btn), doc_.body);
    marks.push({ slug: doc.slug, node, box });
    return node;
  }

  /** Capabilities switched on that no held doc requires — the group-inheritance blind spot. */
  function orphanCard() {
    if (!surface) return null;
    const orphans = surface.filter((n) => n.state === "uncovered");
    if (!orphans.length) return null;
    return el("div", { class: "ability orphan", "data-ability": "(uncovered)" },
      el("div", { class: "ability-head" },
        el("span", {}, "⚑"),
        el("div", {},
          el("div", { class: "ability-name" }, "Switched on by nothing here"),
          el("div", { class: "muted small prose" },
            "The routine may use these, but no conduct doc above asked for them — so it acts "
            + "without the prose that normally comes with them.")),
        el("span", { class: "pill soft" }, `${orphans.length}`)),
      el("ul", { class: "ability-stack" }, ...orphans.map((n) => {
        const [cls, ...rest] = n.id.split(":");
        return stackRow({ state: "note", kind: KIND_LABEL[cls] || cls,
                          entity: rest.join(":"), note: n.why });
      })));
  }

  /** Appearance only: which rows are staged for a change, and whether save is live. */
  function repaint() {
    for (const { slug, node, box } of marks) {
      const staged = held.has(slug) !== committed.has(slug);
      node.classList.toggle("pending", staged);
      node.classList.toggle("pending-drop", staged && committed.has(slug));
      if (box) box.checked = held.has(slug);
    }
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
      const grid = el("div", { class: "abilities" }, ...on.map(card));
      host.append(grid);
      const orphan = orphanCard();
      if (orphan) grid.append(orphan);
    } else {
      host.append(el("div", { class: "muted small" },
        "this routine holds no conduct permissions — it can read, write in its own dir and "
        + "call ungated utils, nothing more"));
    }
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
                    runs: caps.runs, workflows: caps.workflows },
  });

  let footer = null;
  if (opts.onSave) {
    const saveBtn = el("button", { class: "btn primary" }, opts.saveLabel || "save permissions");
    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      try { await opts.onSave(value()); } finally { saveBtn.disabled = false; }
    };
    footer = el("div", { class: "row mt" }, saveBtn);
  }
  return { node: el("div", {}, host, footer), value };
}