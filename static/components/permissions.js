// Two-layer permissions panel: CONDUCT permissions (library docs whose prose reaches the
// run's prompt) beside the machine-enforced CAPABILITIES (gated actions, reserved utils,
// the write_util approval level, previous-run depth). The two cascade: activating a doc
// switches on the capabilities its `requires:` names; switching a capability off
// deactivates the docs that require it. Shared by the routine page and the conversation
// rail — the server re-applies the activation cascade, so the invariant holds either way.

import { el, requiresSummary, toast } from "/static/util.js";

const CONFIRM_OPTIONS = [
  ["off", "off — engine rejects write_util"],
  ["always", "on — every create/revise asks you"],
  ["creations", "on — new utils ask; revisions are autonomous"],
  ["never", "on — fully autonomous (selftest-gated)"],
];
const RUNS_OPTIONS = [
  ["none", "off — previous runs unreadable"],
  ["last", "the last run only"],
  ["all", "all previous runs"],
];
const RUNS_RANK = { none: 0, last: 1, all: 2 };

// permissions: [{slug, summary, requires, active, routine_only?}]
// capabilities: {active: {actions, utils, confirm, runs}, vocabulary: {actions, utils}}
// opts: {onSave(payload), disableRuns?: string (reason), saveLabel?}
export function permissionsPanel(permissions, capabilities, opts = {}) {
  const docs = permissions || [];
  const vocab = capabilities?.vocabulary || { actions: [], utils: [] };
  const held = new Set(docs.filter((p) => p.active && !p.routine_only).map((p) => p.slug));
  const caps = {
    actions: new Set(capabilities?.active?.actions || []),
    utils: new Set(capabilities?.active?.utils || []),
    confirm: capabilities?.active?.confirm || "always",
    runs: capabilities?.active?.runs || "none",
  };

  const needs = (p) => p.requires || {};
  const requiredBy = (test) => [...held].filter((slug) => {
    const r = needs(docs.find((d) => d.slug === slug) || {});
    return test(r);
  });
  // the deactivation cascade: docs whose requires the current mapping no longer covers
  const dropUnsatisfied = () => {
    const dropped = [];
    for (const slug of [...held]) {
      const r = needs(docs.find((d) => d.slug === slug) || {});
      const ok = (r.actions || []).every((a) => caps.actions.has(a))
        && (r.utils || []).every((u) => caps.utils.has(u))
        && (!r.runs || RUNS_RANK[caps.runs] >= RUNS_RANK[r.runs]);
      if (!ok) { held.delete(slug); dropped.push(slug); }
    }
    if (dropped.length) toast(`also deactivated: ${dropped.join(", ")} (their instructions need that capability)`);
  };
  // the activation cascade: raise the mapping to cover one doc's requires
  const raiseFor = (r) => {
    (r.actions || []).forEach((a) => caps.actions.add(a));
    (r.utils || []).forEach((u) => caps.utils.add(u));
    if (r.runs && RUNS_RANK[caps.runs] < RUNS_RANK[r.runs]) caps.runs = r.runs;
  };
  // the inverse of the deactivation cascade (D8): a capability is only ever the MEANS of a
  // held permission, so enabling one holds the permission(s) that grant it. The two layers
  // can then never contradict — and the server floors to the same invariant on save, so a
  // capability the panel shows on is always backed by a held permission.
  const holdCovering = (test) => {
    for (const p of docs) {
      if (!p.routine_only && test(needs(p))) { held.add(p.slug); raiseFor(needs(p)); }
    }
  };

  const docsCol = el("div", {});
  const capsCol = el("div", {});

  function renderDocs() {
    docsCol.replaceChildren(el("div", { class: "lbl", style: "margin-bottom:6px" }, "conduct permissions"),
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "instruction docs from the library — when held, their notes reach the run's prompt; ",
        "activating one switches on the capabilities it needs."));
    for (const p of docs) {
      const box = el("input", { type: "checkbox", checked: held.has(p.slug) ? "" : null,
        disabled: p.routine_only ? "" : null });
      box.onchange = () => {
        if (box.checked) { held.add(p.slug); raiseFor(needs(p)); }
        else held.delete(p.slug);
        render();
      };
      const req = requiresSummary(p.requires);
      docsCol.append(el("label", { class: `toggle-row${p.routine_only ? " faint" : ""}`,
        title: p.routine_only ? "only meaningful for scheduled routines" : "" }, box,
        el("div", {},
          el("div", { class: "t-title" }, p.slug, p.routine_only ? " (routines only)" : ""),
          el("div", { class: "muted prose small" }, p.summary || ""),
          req ? el("div", { class: "small", style: "color:var(--warn)" }, `▸ ${req}`) : null)));
    }
    if (!docs.length) docsCol.append(el("div", { class: "muted" }, "no permissions in the library"));
  }

  const badge = (slugs) => slugs.length
    ? el("div", { class: "muted small" }, `required by ${slugs.join(", ")}`) : null;
  const capRow = (control, title, help, reqBadge) =>
    el("div", { class: "toggle-row", style: "align-items:flex-start" }, control,
      el("div", {},
        el("div", { class: "t-title" }, title),
        help ? el("div", { class: "muted prose small" }, help) : null,
        reqBadge));

  function renderCaps() {
    capsCol.replaceChildren(el("div", { class: "lbl", style: "margin-bottom:6px" }, "capabilities"),
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "the machine-enforced surface — what the engine actually permits, checked on every ",
        "action. Switching one off also deactivates the permissions that need it."));
    for (const a of vocab.actions || []) {
      if (a === "write_util") {
        const sel = el("select", {}, ...CONFIRM_OPTIONS.map(([v, label]) =>
          el("option", { value: v, selected: (caps.actions.has("write_util") ? caps.confirm : "off") === v ? "" : null }, label)));
        sel.onchange = () => {
          if (sel.value === "off") { caps.actions.delete("write_util"); dropUnsatisfied(); }
          else { caps.actions.add("write_util"); caps.confirm = sel.value;
                 holdCovering((r) => (r.actions || []).includes("write_util")); }
          render();
        };
        capsCol.append(capRow(sel, "write_util — author global utils",
          "create/revise shared utils; the level is your approval policy",
          badge(requiredBy((r) => (r.actions || []).includes("write_util")))));
      } else {
        const box = el("input", { type: "checkbox", checked: caps.actions.has(a) ? "" : null });
        box.onchange = () => {
          if (box.checked) { caps.actions.add(a); holdCovering((r) => (r.actions || []).includes(a)); }
          else { caps.actions.delete(a); dropUnsatisfied(); }
          render();
        };
        capsCol.append(capRow(box, `${a} — action`,
          a.startsWith("memory") ? "the .memory/ notebook of hard-won facts" : "",
          badge(requiredBy((r) => (r.actions || []).includes(a)))));
      }
    }
    for (const u of vocab.utils || []) {
      const box = el("input", { type: "checkbox", checked: caps.utils.has(u) ? "" : null });
      box.onchange = () => {
        if (box.checked) { caps.utils.add(u); holdCovering((r) => (r.utils || []).includes(u)); }
        else { caps.utils.delete(u); dropUnsatisfied(); }
        render();
      };
      capsCol.append(capRow(box, `util ${u} — reserved channel`, "",
        badge(requiredBy((r) => (r.utils || []).includes(u)))));
    }
    const runsSel = el("select", { disabled: opts.disableRuns ? "" : null },
      ...RUNS_OPTIONS.map(([v, label]) =>
        el("option", { value: v, selected: caps.runs === v ? "" : null }, label)));
    runsSel.onchange = () => {
      caps.runs = runsSel.value;
      if (caps.runs !== "none") holdCovering((r) => !!r.runs);
      dropUnsatisfied();
      render();
    };
    capsCol.append(capRow(runsSel, "previous runs — read depth",
      opts.disableRuns || "read_file on earlier runs' transcripts and results under runs/",
      badge(requiredBy((r) => !!r.runs))));
  }

  function render() { renderDocs(); renderCaps(); }
  render();

  const saveBtn = el("button", { class: "btn primary" }, opts.saveLabel || "save permissions");
  saveBtn.onclick = async () => {
    saveBtn.disabled = true;
    try {
      await opts.onSave({
        active: docs.filter((p) => p.routine_only ? p.active : held.has(p.slug)).map((p) => p.slug),
        capabilities: { actions: [...caps.actions], utils: [...caps.utils],
                        confirm: caps.confirm, runs: caps.runs },
      });
    } finally { saveBtn.disabled = false; }
  };

  return el("div", {},
    el("div", { style: "display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px" },
      docsCol, capsCol),
    el("div", { class: "row mt" }, saveBtn));
}
