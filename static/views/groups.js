// Groups: named, ordered collections of routines with a mid-chain-failure policy (D53).
// This page is the CRUD surface over /api/groups (the .control/groups.json store) — create,
// name, ORDER members, choose stop-vs-continue on a mid-chain failure (per-group, or inherit the
// instance default), and delete. Phase B (live): "Run now" arms a sequential fire
// (POST /api/groups/{id}/run) that the daemon advances one member per tick; the page shows an
// in-flight chain's progress from the /api/groups `in_flight` map.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, emptyState, skeleton, toast } from "/static/util.js";

export async function render(view) {
  const box = el("div", { class: "panel" });
  box.append(skeleton(["40%", "90%", "70%"]));
  view.append(box);

  async function load() {
    let d;
    try { d = await api("/api/groups"); }
    catch (err) { box.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    box.replaceChildren();
    box.append(el("h2", {}, "Routine groups"));
    box.append(el("div", { class: "muted small", style: "margin-bottom:10px" },
      "A group runs its routines in order, one after another. Choose what happens if a member ",
      "fails partway through — stop the rest of the chain, or carry on. Press ",
      el("strong", {}, "Run now"),
      " on a group to fire its members sequentially; each starts once the previous one finishes."));

    // Instance default on-failure.
    const defSel = el("select", { "data-groups-default": "" },
      ...d.on_failure_vocab.map((v) =>
        el("option", { value: v, ...(v === d.default_on_failure ? { selected: "" } : {}) }, v)));
    defSel.onchange = async () => {
      try { await api("/api/groups/default", { method: "PUT", body: { default_on_failure: defSel.value } });
        toast(`default on failure → ${defSel.value}`); }
      catch (err) { toast(err.message, 4000, { error: true }); load(); }
    };
    box.append(el("div", { class: "row mt", style: "gap:8px;align-items:center" },
      el("span", { class: "small", style: "font-weight:600" }, "Default on mid-chain failure:"), defSel,
      el("span", { class: "muted small" }, "applies to any group set to “inherit”")));

    // Existing groups.
    const list = el("div", { class: "mt" });
    if (!d.groups.length) list.append(emptyState("⛓", "no groups yet", "Add one below."));
    else for (const g of d.groups) list.append(groupCard(g, d));
    box.append(list);

    // Add-group form.
    const nameIn = el("input", { type: "text", placeholder: "group name", style: "width:200px" });
    const picker = el("select", { multiple: "", size: "5", style: "min-width:220px",
      "data-group-members": "" },
      ...d.known_routines.map((r) => el("option", { value: r.slug }, r.name)));
    const ofSel = el("select", { "data-group-onfailure": "" },
      el("option", { value: "" }, "inherit default"),
      ...d.on_failure_vocab.map((v) => el("option", { value: v }, v)));
    const addBtn = el("button", { class: "btn primary" }, "add group");
    addBtn.onclick = async () => {
      const name = nameIn.value.trim();
      if (!name) { toast("a group name is required"); return; }
      const members = [...picker.selectedOptions].map((o) => o.value);
      const body = { name, members, on_failure: ofSel.value || null };
      try { await api("/api/groups", { method: "POST", body });
        toast(`group “${name}” added`); nameIn.value = ""; load(); }
      catch (err) { toast(err.message, 5000, { error: true }); }
    };
    box.append(
      el("div", { class: "mt small", style: "font-weight:600" }, "Add a group"),
      el("div", { class: "row mt", style: "flex-wrap:wrap;gap:8px;align-items:flex-start" },
        nameIn,
        el("label", { class: "small" }, el("div", { class: "muted" },
          "members (Ctrl/⌘-click, drag to multi-select — order is set with ↑↓ after adding)"), picker),
        el("label", { class: "small" }, el("div", { class: "muted" }, "on failure"), ofSel),
        addBtn));
  }

  function groupCard(g, d) {
    const card = el("div", { class: "panel mt", "data-group": g.id });
    const nameLabel = el("strong", {}, g.name);
    const effective = g.on_failure || `${d.default_on_failure} (inherited)`;

    // Ordered member rows with ↑/↓ reorder + a per-member remove; empty groups say so.
    const rows = el("div", {});
    const rerender = () => {
      rows.replaceChildren();
      if (!g.members.length) rows.append(el("div", { class: "muted small" }, "no members"));
      g.members.forEach((slug, i) => {
        const up = el("button", { class: "btn small", ...(i === 0 ? { disabled: "" } : {}) }, "↑");
        const down = el("button", { class: "btn small",
          ...(i === g.members.length - 1 ? { disabled: "" } : {}) }, "↓");
        const rm = el("button", { class: "btn small danger" }, "remove");
        up.onclick = () => { [g.members[i - 1], g.members[i]] = [g.members[i], g.members[i - 1]]; save(); };
        down.onclick = () => { [g.members[i + 1], g.members[i]] = [g.members[i], g.members[i + 1]]; save(); };
        rm.onclick = () => { g.members.splice(i, 1); save(); };
        rows.append(el("div", { class: "row", style: "gap:6px;align-items:center", "data-member": slug },
          el("span", { class: "small mono", style: "width:22px" }, `${i + 1}.`),
          el("span", { class: "small" }, slug), up, down, rm));
      });
    };
    const save = async () => {
      try { await api(`/api/groups/${g.id}`, { method: "PATCH", body: { members: g.members } });
        rerender(); toast("order saved"); }
      catch (err) { toast(err.message, 4000, { error: true }); load(); }
    };
    rerender();

    // Per-group on-failure override.
    const ofSel = el("select", { "data-group-onfailure": "" },
      el("option", { value: "", ...(g.on_failure ? {} : { selected: "" }) }, "inherit default"),
      ...d.on_failure_vocab.map((v) =>
        el("option", { value: v, ...(g.on_failure === v ? { selected: "" } : {}) }, v)));
    ofSel.onchange = async () => {
      try { await api(`/api/groups/${g.id}`,
          { method: "PATCH", body: { on_failure: ofSel.value || null, set_on_failure: true } });
        toast("on-failure saved"); load(); }
      catch (err) { toast(err.message, 4000, { error: true }); load(); }
    };

    const del = el("button", { class: "btn small danger" }, "delete group");
    del.onclick = async () => {
      if (!(await confirmDialog(`Delete group “${g.name}”?`, { confirmLabel: "delete" }))) return;
      try { await api(`/api/groups/${g.id}`, { method: "DELETE" }); toast("group deleted"); load(); }
      catch (err) { toast(err.message, 4000, { error: true }); }
    };

    // Run now — arm a sequential fire; disabled (with a progress line) while a chain is in flight.
    const flight = (d.in_flight || {})[g.id];
    const runBtn = el("button", { class: "btn small primary", "data-group-run": "",
      ...(flight || !g.members.length ? { disabled: "" } : {}) }, "Run now");
    runBtn.onclick = async () => {
      try { await api(`/api/groups/${g.id}/run`, { method: "POST" });
        toast(`group “${g.name}” firing`); load(); }
      catch (err) { toast(err.message, 4000, { error: true }); load(); }
    };
    const progress = flight
      ? el("div", { class: "muted small mt", "data-group-progress": "" },
          `running ${Math.min((flight.cursor || 0) + 1, flight.members.length)}/${flight.members.length}`,
          flight.members[flight.cursor] ? ` · ${flight.members[flight.cursor]}` : " · finishing…")
      : null;

    card.append(
      el("div", { class: "row", style: "justify-content:space-between;align-items:center" },
        nameLabel, el("div", { class: "row", style: "gap:6px" }, runBtn, del)),
      el("div", { class: "muted small mt", "data-group-effective": "" }, `on failure: ${effective}`),
      ...(progress ? [progress] : []),
      el("div", { class: "mt" }, rows),
      el("div", { class: "row mt", style: "gap:8px;align-items:center" },
        el("span", { class: "small" }, "on failure:"), ofSel));
    return card;
  }

  await load();
  return null;
}
