// The setup a picked playbook PROPOSES, shown for one explicit confirmation (D313).
//
// The operator was asked how a conversation should apply the selected playbook's setup and chose
// (2026-11-25): "pre-fill and show them at creation; one click to confirm". He declined applying
// them silently, and he declined pre-filling only the harmless ones — so a shell-bearing
// playbook's `permissions: [shell]` IS shown here, and what protects it is this panel being read
// and confirmed rather than the field being withheld.
//
// NOTHING TAKES EFFECT BEFORE THE CONFIRMATION, and that is a property of this module rather
// than a rule a caller must remember: `value()` returns an EMPTY object until `confirm` is
// clicked. The composer reads `value()` when the conversation is sent, so there is no code path
// by which selecting a playbook changes a setting. Deselecting it, or picking another, calls
// `render` again and the confirmation is gone with the proposal it was about.
//
// EDITABLE, because a pre-fill a person cannot refuse is an application with extra steps. Every
// proposed item is a ticked checkbox; unticking one leaves it out of `value()` entirely, so an
// unselected field applies nothing at all.
//
// WHAT IT NEVER CARRIES: an account. `setup.connections` arrives from the server as provider
// NAMES (rsched/playbook_setup.py) — there is no account label to pre-fill and no field here
// that could hold one, so a confirmed proposal cannot bind an identity. The panel says which
// providers the procedure needs and points at the composer's own connections card, where the
// accounts actually connected are picked.
//
// WHAT IT IS NOT: a second door. A confirmed value is merged into the ORDINARY create form
// fields by views/conversations-new.js and travels the ordinary validators — the rules are
// checked against the library, the roots through the one grant enforcer, the permissions through
// the same validate-cascade-floor the routine page saves through. This panel has no endpoint.

import { el } from "/static/util.js";

//: the proposed fields, in reading order: what it may DO, then what it practises, then where.
const FIELDS = [
  ["permissions", "permissions", "what this conversation is allowed to do"],
  ["rules", "general rules", "the conduct it practises"],
  ["fs_write_roots", "write folders", "read + write access"],
  ["fs_read_roots", "read folders", "read-only access"],
];

export function playbookSetup() {
  // The state is stamped from the START, not from the first render: "no playbook is picked" and
  // "this panel has not loaded" are different facts, and a browser case asserting that nothing
  // is proposed yet has to be able to tell them apart (found by
  // tests/ui/test_playbook_setup_confirm.py, which waited 15s for an attribute that only
  // appeared once something had been selected).
  const host = el("div", { "data-pb-setup-state": "none" });
  let boxes = [];           // [{field, item, box}] — one per proposed value
  let workdirBox = null;    // the single-valued field, so it is its own control
  let setup = null;
  let confirmed = false;

  const picked = (field) => boxes.filter((b) => b.field === field && b.box.checked)
    .map((b) => b.item);

  const row = (field, label, why, items) => el("div", { class: "pb-setup-field" },
    el("div", { class: "pb-setup-label" }, label,
      el("span", { class: "pb-setup-why" }, ` — ${why}`)),
    el("div", { class: "pb-setup-items" }, ...items.map((item) => {
      const box = el("input", { type: "checkbox", checked: true, "data-nopersist": "",
        "data-pb-setup": `${field}:${item}` });
      boxes.push({ field, item, box });
      return el("label", { class: "pb-setup-item" }, box, el("span", {}, item));
    })));

  return {
    node: host,

    /** What the person CONFIRMED, as the create form's own fields. Empty until they do. */
    value() {
      if (!confirmed || !setup) return {};
      const out = {};
      for (const [field] of FIELDS) {
        const vals = picked(field);
        if (vals.length) out[field] = vals;
      }
      if (workdirBox?.checked && setup.workdir) out.workdir = setup.workdir;
      return out;
    },

    /** True once the panel has been confirmed — the composer asks before merging anything. */
    get confirmed() { return confirmed; },

    /**
     * Show the proposal of the playbook just picked, UNCONFIRMED. Called with the catalog
     * entry's `setup` block, or with nothing when the selection was cleared — either way any
     * earlier confirmation is dropped, because it was about a different playbook.
     */
    render(proposed) {
      setup = proposed || null;
      confirmed = false;
      boxes = [];
      workdirBox = null;
      // The count is stamped whether or not anything mounts, so a browser case asserting that
      // an unconfirmed panel applied NOTHING waits for this render instead of passing before
      // the catalog answered (the trap components/playbook-picker.js records at `data-pb-count`).
      host.setAttribute("data-pb-setup-state", setup?.declares ? "proposed" : "none");
      if (!setup?.declares) { host.replaceChildren(); return; }

      const rows = [];
      for (const [field, label, why] of FIELDS) {
        if (setup[field]?.length) rows.push(row(field, label, why, setup[field]));
      }
      if (setup.workdir) {
        workdirBox = el("input", { type: "checkbox", checked: true, "data-nopersist": "",
          "data-pb-setup": `workdir:${setup.workdir}` });
        rows.push(el("div", { class: "pb-setup-field" },
          el("div", { class: "pb-setup-label" }, "project directory",
            el("span", { class: "pb-setup-why" }, " — write root #1")),
          el("div", { class: "pb-setup-items" },
            el("label", { class: "pb-setup-item" }, workdirBox,
              el("span", {}, setup.workdir)))));
      }
      // Providers, stated and NOT pre-filled: naming one is not binding it, and the account is
      // the identity a reply would act as. So this row is prose pointing at the composer's own
      // connections card rather than a control.
      if (setup.connections?.length) {
        rows.push(el("div", { class: "pb-setup-field" },
          el("div", { class: "pb-setup-label" }, "connections",
            el("span", { class: "pb-setup-why" },
              " — bind the account yourself under Connections below; a playbook names the "
              + "provider it needs and never carries an account")),
          el("div", { class: "pb-setup-items" },
            el("span", { class: "pb-setup-item" }, setup.connections.join(", ")))));
      }
      // Parameters the playbook left as {{placeholders}}: dropped by the server rather than
      // offered as a literal path, and named here so the person fills them in themselves.
      if (setup.unresolved?.length) {
        rows.push(el("div", { class: "pb-setup-note" },
          `fill in yourself: ${setup.unresolved.join(", ")} — this playbook leaves them as `
          + "parameters, so nothing was pre-filled for them"));
      }
      if (setup.unknown?.length) {
        rows.push(el("div", { class: "pb-setup-note" },
          `not offered (the library does not hold it): ${setup.unknown.join(", ")}`));
      }
      if (setup.note) rows.push(el("div", { class: "pb-setup-note" }, setup.note));

      const state = el("div", { class: "pb-setup-state" },
        "nothing is applied yet — these take effect when you confirm");
      const confirm = el("button", { type: "button", class: "btn small pb-setup-confirm" },
        "confirm this setup");
      confirm.onclick = () => {
        confirmed = true;
        confirm.disabled = true;
        confirm.textContent = "✓ setup confirmed";
        host.setAttribute("data-pb-setup-state", "confirmed");
        state.textContent = "confirmed — the ticked settings start this conversation; "
          + "untick one and it is left out";
      };
      host.replaceChildren(el("div", { class: "pb-setup" },
        el("div", { class: "pb-setup-head" }, "This playbook needs"),
        ...rows,
        el("div", { class: "pb-setup-foot" }, confirm, state)));
    },
  };
}
