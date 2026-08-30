// Group management on the Routines page (D80 — the /groups subpage is retired; the group
// rows in the routines table are the one management surface). Three pieces:
//   groupsToolbar  — "+ new group" + the instance default-on-failure select
//   groupControls  — the per-group-row buttons: ▶ run now, ⏸ pause/resume, ✎ edit
//   openGroupEditor / openGroupCreate — OVERLAY panels for editing/creating a group.
// The editors are overlays on purpose: the dashboard re-renders its body on every bus tick
// (debounced 600ms while anything runs), which would tear down inline inputs mid-typing
// (the F229 lesson) — an overlay lives outside that cycle and closes on its own terms.
// Every mutation goes through /api/groups (the .control/groups.json store); a member is a
// RECORD {slug}. A flow with an inbound and an outbound end brackets the group (D90):
// an inbound-router member placed first in the order, an outbound-sender member placed last.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { groupConfigPanel } from "/static/components/groupconfig.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { el, toast } from "/static/util.js";


const err = (e) => toast(e.message, 4000, { error: true });

// ---- the per-row controls --------------------------------------------------------------------

/** The group row's management buttons. `data` is the /api/groups payload; `reload` refreshes
 *  the dashboard after a mutation. Returns an array of nodes (some conditional). */
export function groupControls(g, data, { reload }) {
  const flight = (data.in_flight || {})[g.id];
  const run = el("button", { class: "btn small primary", "data-group-run": "",
    title: flight ? "a chain is already in flight" : "fire the members in order now",
    ...(flight || !(g.members || []).length ? { disabled: "" } : {}) }, "▶ run now");
  run.onclick = async (e) => {
    e.stopPropagation();
    try { await api(`/api/groups/${g.id}/run`, { method: "POST" });
      toast(`group “${g.name}” firing`); reload(); }
    catch (ex) { err(ex); reload(); }
  };
  // Whole-group pause gates the cron only — nothing to pause on an unscheduled group.
  const pause = g.cron
    ? el("button", { class: "btn small", "data-group-pause-toggle": "",
        title: g.paused ? "resume this group's schedule"
          : "stop the schedule from firing this group — ▶ run now still works" },
        g.paused ? "▶ resume" : "⏸ pause")
    : null;
  if (pause) pause.onclick = async (e) => {
    e.stopPropagation();
    try { await api(`/api/groups/${g.id}`, { method: "PATCH", body: { paused: !g.paused } });
      toast(g.paused ? `group “${g.name}” resumed` : `group “${g.name}” paused`); reload(); }
    catch (ex) { err(ex); reload(); }
  };
  const edit = el("button", { class: "btn small ghost", "data-group-edit": "",
    title: "edit this group: members, order, schedule, on-failure, delete" }, "✎");
  edit.onclick = (e) => { e.stopPropagation(); openGroupEditor(g, data, { reload }); };
  return [run, ...(pause ? [pause] : []), edit];
}

/** One line of in-flight chain progress for the group row, or null. */
export function groupProgress(g, data) {
  const flight = (data.in_flight || {})[g.id];
  if (!flight) return null;
  const list = (flight.members || []).map((m) => m.slug);
  const at = Math.min((flight.cursor || 0) + 1, list.length);
  return el("span", { class: "muted small", "data-group-progress": "" },
    `· ${at}/${list.length}`,
    list[flight.cursor] ? ` · ${list[flight.cursor]}` : " · finishing…");
}

// ---- the toolbar -----------------------------------------------------------------------------

/** "+ new group" + the instance default-on-failure select — rendered once above the routine
 *  list (rebuilt only when the groups payload changes, like the filter bar). */
export function groupsToolbar(data, { reload }) {
  const add = el("button", { class: "btn small", "data-group-new": "",
    title: "a group runs its routines in order, one after another" }, "＋ new group");
  add.onclick = () => openGroupCreate(data, { reload });
  const defSel = el("select", { "data-groups-default": "" },
    ...data.on_failure_vocab.map((v) =>
      el("option", { value: v, ...(v === data.default_on_failure ? { selected: "" } : {}) }, v)));
  defSel.onchange = async () => {
    try { await api("/api/groups/default", { method: "PUT",
        body: { default_on_failure: defSel.value } });
      toast(`default on failure → ${defSel.value}`); reload(); }
    catch (ex) { err(ex); reload(); }
  };
  return el("div", { class: "row", style: "gap:8px;align-items:center;flex-wrap:wrap" },
    el("span", { class: "lbl" }, "⛓ groups"), add,
    el("span", { class: "muted small", style: "margin-left:8px" }, "default on mid-chain failure:"),
    defSel,
    el("span", { class: "muted small" }, "applies to any group set to “inherit”"));
}

// ---- the overlay editors ---------------------------------------------------------------------

function overlay(title, body, { onClose }) {
  const close = el("button", { class: "btn small ghost", "data-group-editor-close": "" }, "✕ close");
  const wrap = el("div", { class: "modal-overlay" },
    el("div", { class: "panel", role: "dialog", "aria-modal": "true",
      style: "min-width:min(560px,92vw);max-width:92vw;max-height:88vh;overflow:auto" },
      el("div", { class: "row", style: "justify-content:space-between;align-items:center" },
        el("strong", {}, title), close),
      body));
  const done = () => { wrap.remove(); onClose?.(); };
  close.onclick = done;
  // no overlay-click close: an accidental click must not discard half-edited state
  wrap.onkeydown = (e) => { if (e.key === "Escape") { e.preventDefault(); done(); } };
  document.body.append(wrap);
  return { close: done };
}

/** The create form: name + members (order via the editor afterwards) + on-failure. */
function openGroupCreate(data, { reload }) {
  const nameIn = el("input", { type: "text", placeholder: "group name",
    "data-group-new-name": "", "data-nopersist": true, style: "width:220px" });
  const picker = el("select", { multiple: "", size: "6", style: "min-width:240px",
    "data-group-members": "" },
    ...data.known_routines.map((r) => el("option", { value: r.slug }, r.name)));
  const ofSel = el("select", { "data-group-onfailure": "" },
    el("option", { value: "" }, "inherit default"),
    ...data.on_failure_vocab.map((v) => el("option", { value: v }, v)));
  const addBtn = el("button", { class: "btn primary" }, "add group");
  const body = el("div", { class: "mt" },
    el("div", { class: "muted small", style: "margin-bottom:8px" },
      "A group runs its routines in order, one after another. For a flow with an inbound "
      + "and an outbound end, bracket the group: an inbound-router routine first, an "
      + "outbound-sender routine last. Fire order and the group schedule are edited on "
      + "the group after it is created."),
    el("div", { class: "row", style: "flex-wrap:wrap;gap:10px;align-items:flex-start" },
      el("label", { class: "small" }, el("div", { class: "muted" }, "name"), nameIn),
      el("label", { class: "small" }, el("div", { class: "muted" },
        "members (Ctrl/⌘-click to multi-select)"), picker),
      el("label", { class: "small" }, el("div", { class: "muted" }, "on failure"), ofSel)),
    el("div", { class: "row mt" }, addBtn));
  const ov = overlay("New group", body, { onClose: null });
  addBtn.onclick = async () => {
    const name = nameIn.value.trim();
    if (!name) { toast("a group name is required"); return; }
    const members = [...picker.selectedOptions].map((o) => ({ slug: o.value }));
    try {
      await api("/api/groups", { method: "POST",
        body: { name, members, on_failure: ofSel.value || null } });
      toast(`group “${name}” added`); ov.close(); reload();
    } catch (ex) { err(ex); }
  };
}

/** The per-group editor: rename, ordered members with split flags, add/remove, on-failure,
 *  schedule, delete. Saves apply immediately (each control PATCHes) and the panel re-renders
 *  from the response, so it never goes stale against its own writes. */
export function openGroupEditor(group, data, { reload }) {
  let g = group;                     // updated from every PATCH response
  const body = el("div", { class: "mt", "data-group": g.id });
  const ov = overlay(`Group “${g.name}”`, body, { onClose: reload });

  // A save's `warnings` name ORPHAN capabilities — switched on in the group's shared config
  // with no permission in that config requiring them, so they reach only the members that
  // happen to hold a covering doc themselves. Legal, nearly always a mistake — and impossible
  // to notice anywhere downstream: the server has said this since the shared-config editor
  // shipped and nothing had ever shown it. They survive the re-render, so the operator reads
  // them after the panel repaints rather than losing them to it.
  let warnings = [];
  const patch = async (fields) => {
    try {
      const r = await api(`/api/groups/${g.id}`, { method: "PATCH", body: fields });
      g = r.group;
      warnings = r.warnings || [];
      render();
    } catch (ex) { err(ex); render(); }
  };
  const saveMembers = () => patch({ members: g.members });

  function render() {
    body.replaceChildren();
    if (warnings.length) {
      body.append(el("div", { class: "panel warn", "data-group-warnings": "" },
        el("div", { class: "small" }, el("b", {}, "⚠ switched on, but nothing here asks for it")),
        el("ul", { class: "small", style: "margin:6px 0 0;padding-left:18px" },
          warnings.map((w) => el("li", {}, w)))));
    }

    // rename — applies on change (blur/Enter), like every other immediate-save control here
    const nameIn = el("input", { type: "text", value: g.name, "data-group-name": "",
      "data-nopersist": true, style: "width:220px" });
    nameIn.onchange = () => { if (nameIn.value.trim()) patch({ name: nameIn.value.trim() }); };
    body.append(el("label", { class: "small" },
      el("div", { class: "muted" }, "name"), nameIn));

    // ordered member rows: ↑/↓ reorder, remove
    const rows = el("div", { class: "mt" });
    if (!(g.members || []).length) rows.append(el("div", { class: "muted small" }, "no members"));
    (g.members || []).forEach((m, i) => {
      const up = el("button", { class: "btn small", ...(i === 0 ? { disabled: "" } : {}) }, "↑");
      const down = el("button", { class: "btn small",
        ...(i === g.members.length - 1 ? { disabled: "" } : {}) }, "↓");
      const rm = el("button", { class: "btn small danger" }, "remove");
      up.onclick = () => { [g.members[i - 1], g.members[i]] = [g.members[i], g.members[i - 1]]; saveMembers(); };
      down.onclick = () => { [g.members[i + 1], g.members[i]] = [g.members[i], g.members[i + 1]]; saveMembers(); };
      rm.onclick = () => { g.members.splice(i, 1); saveMembers(); };
      rows.append(el("div", { class: "row", style: "gap:6px;align-items:center",
        "data-member": m.slug },
        el("span", { class: "small mono", style: "width:22px" }, `${i + 1}.`),
        el("span", { class: "small", style: "min-width:160px" }, m.slug),
        up, down, rm));
    });
    body.append(rows);

    // add a member (routines not already in the group)
    const present = new Set((g.members || []).map((m) => m.slug));
    const addable = data.known_routines.filter((r) => !present.has(r.slug));
    if (addable.length) {
      const sel = el("select", { "data-group-add-member": "" },
        ...addable.map((r) => el("option", { value: r.slug }, r.name)));
      const addBtn = el("button", { class: "btn small" }, "add member");
      addBtn.onclick = () => {
        g.members = [...(g.members || []), { slug: sel.value }];
        saveMembers();
      };
      body.append(el("div", { class: "row mt", style: "gap:6px;align-items:center" },
        sel, addBtn));
    }

    // per-group on-failure override
    const ofSel = el("select", { "data-group-onfailure": "" },
      el("option", { value: "", ...(g.on_failure ? {} : { selected: "" }) }, "inherit default"),
      ...data.on_failure_vocab.map((v) =>
        el("option", { value: v, ...(g.on_failure === v ? { selected: "" } : {}) }, v)));
    ofSel.onchange = () => patch({ on_failure: ofSel.value || null, set_on_failure: true });
    body.append(el("div", { class: "row mt", style: "gap:8px;align-items:center" },
      el("span", { class: "small" }, "on failure:"), ofSel,
      el("span", { class: "muted small" },
        `inherit = the instance default (${data.default_on_failure})`)));

    // D71: the group schedule — fires the chain on this cadence; member crons suppressed.
    const sched = scheduleEditor(g.schedule_friendly || { frequency: "manual" }, data.server_tz);
    const schedBtn = el("button", { class: "btn small", "data-group-schedule-save": "" },
      "save schedule");
    schedBtn.onclick = () => patch({ schedule: { friendly: sched.value() } });
    body.append(el("div", { class: "mt", "data-group-schedule": "" },
      el("div", { class: "small", style: "font-weight:600" }, "Schedule"),
      el("div", { class: "muted small" },
        "fires the chain on this cadence — members run in order; each member's own "
        + "schedule is suppressed while this is set"),
      sched.node,
      el("div", { class: "row mt" }, schedBtn)));

    // D82: the shared routine config every member inherits. Collapsed by default — it is the
    // occasional edit, while membership and schedule are the frequent ones.
    const cfgKeys = Object.keys(g.config || {}).length;
    body.append(el("details", { class: "mt", "data-group-config-section": "" },
      el("summary", { class: "small", style: "font-weight:600;cursor:pointer" },
        `Shared config${cfgKeys ? ` (${cfgKeys} set)` : ""}`),
      groupConfigPanel(g, { save: (config) => patch({ config }) })));

    // delete — closes the editor; the dashboard reloads via onClose
    const del = el("button", { class: "btn small danger" }, "delete group");
    del.onclick = async () => {
      if (!(await confirmDialog(`Delete group “${g.name}”?`, { confirmLabel: "delete" }))) return;
      try { await api(`/api/groups/${g.id}`, { method: "DELETE" });
        toast("group deleted"); ov.close(); }
      catch (ex) { err(ex); }
    };
    body.append(el("div", { class: "row mt", style: "justify-content:flex-end" }, del));
  }

  render();
}
