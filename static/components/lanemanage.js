// Lane management on the Routines page (D80 — the lane rows in the routines table are the one
// management surface a lane has). Three pieces:
//   lanesToolbar  — "＋ new lane" + the instance default-on-failure select
//   laneControls  — the per-lane-row buttons: ▶ run now, ⏸ pause/resume, ✎ edit
//   openLaneEditor / openLaneCreate — OVERLAY panels for editing/creating a lane.
// The editors are overlays on purpose: the dashboard re-renders its body on every bus tick
// (debounced 600ms while anything runs), which would tear down inline inputs mid-typing
// (the F229 lesson) — an overlay lives outside that cycle and closes on its own terms.
// Every mutation goes through /api/lanes (the .control/lanes.json store); a member is a
// RECORD {slug}. A flow with an inbound and an outbound end brackets the lane (D90):
// an inbound-router member placed first in the order, an outbound-sender member placed last.
//
// A lane decides WHEN and IN WHAT ORDER — nothing else. The shared config block, the
// shared store and the boundary domain notes rest on are a DOMAIN (docs/lanes-domains.md) —
// named by each routine's own routine.yaml and edited in the Domains section of this page.
// That is why there is no config control in this editor; it is also what makes deleting a
// lane safe: its members go back to their own crons and nothing else about them changes.
//
// A routine belongs to at most ONE lane and the store enforces it, so the member pickers here
// offer only unclaimed routines — a choice whose only possible outcome is a 400 is not one.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { el, toast } from "/static/util.js";


const err = (e) => toast(e.message, 4000, { error: true });

/** Slugs some OTHER lane already holds (`skip` = the lane being edited). The exclusivity the
 *  store enforces, applied to the pickers so it reads as an absence rather than a rejection. */
function claimedElsewhere(data, skip = "") {
  const out = new Set();
  for (const lane of data.lanes || []) {
    if (lane.id === skip) continue;
    for (const m of lane.members || []) out.add(m.slug);
  }
  return out;
}

// ---- the per-row controls --------------------------------------------------------------------

/** The lane row's management buttons. `data` is the /api/lanes payload; `reload` refreshes
 *  the dashboard after a mutation. Returns an array of nodes (some conditional). */
export function laneControls(lane, data, { reload }) {
  const flight = (data.in_flight || {})[lane.id];
  const run = el("button", { class: "btn small primary", "data-lane-run": "",
    title: flight ? "a chain is already in flight" : "fire the members in order now",
    ...(flight || !(lane.members || []).length ? { disabled: "" } : {}) }, "▶ run now");
  run.onclick = async (e) => {
    e.stopPropagation();
    try { await api(`/api/lanes/${lane.id}/run`, { method: "POST" });
      toast(`lane “${lane.name}” firing`); reload(); }
    catch (ex) { err(ex); reload(); }
  };
  // Whole-lane pause gates the cron only — nothing to pause on an unscheduled lane.
  const pause = lane.cron
    ? el("button", { class: "btn small", "data-lane-pause-toggle": "",
        title: lane.paused ? "resume this lane's schedule"
          : "stop the schedule from firing this lane — ▶ run now still works" },
        lane.paused ? "▶ resume" : "⏸ pause")
    : null;
  if (pause) pause.onclick = async (e) => {
    e.stopPropagation();
    try { await api(`/api/lanes/${lane.id}`, { method: "PATCH", body: { paused: !lane.paused } });
      toast(lane.paused ? `lane “${lane.name}” resumed` : `lane “${lane.name}” paused`); reload(); }
    catch (ex) { err(ex); reload(); }
  };
  const edit = el("button", { class: "btn small ghost", "data-lane-edit": "",
    title: "edit this lane: members, order, schedule, on-failure, delete" }, "✎");
  edit.onclick = (e) => { e.stopPropagation(); openLaneEditor(lane, data, { reload }); };
  return [run, ...(pause ? [pause] : []), edit];
}

/** One line of in-flight chain progress for the lane row, or null. */
export function laneProgress(lane, data) {
  const flight = (data.in_flight || {})[lane.id];
  if (!flight) return null;
  const list = (flight.members || []).map((m) => m.slug);
  const at = Math.min((flight.cursor || 0) + 1, list.length);
  return el("span", { class: "muted small", "data-lane-progress": "" },
    `· ${at}/${list.length}`,
    list[flight.cursor] ? ` · ${list[flight.cursor]}` : " · finishing…");
}

// ---- the toolbar -----------------------------------------------------------------------------

/** "＋ new lane" + the instance default-on-failure select — rendered once above the routine
 *  list (rebuilt only when the lanes payload changes, like the filter bar). */
export function lanesToolbar(data, { reload }) {
  const add = el("button", { class: "btn small", "data-lane-new": "",
    title: "a lane runs its routines in order, one after another" }, "＋ new lane");
  add.onclick = () => openLaneCreate(data, { reload });
  const defSel = el("select", { "data-lanes-default": "" },
    ...data.on_failure_vocab.map((v) =>
      el("option", { value: v, ...(v === data.default_on_failure ? { selected: "" } : {}) }, v)));
  defSel.onchange = async () => {
    try { await api("/api/lanes/default", { method: "PUT",
        body: { default_on_failure: defSel.value } });
      toast(`default on failure → ${defSel.value}`); reload(); }
    catch (ex) { err(ex); reload(); }
  };
  return el("div", { class: "row", style: "gap:8px;align-items:center;flex-wrap:wrap" },
    el("span", { class: "lbl" }, "⛓ lanes"), add,
    el("span", { class: "muted small", style: "margin-left:8px" }, "default on mid-chain failure:"),
    defSel,
    el("span", { class: "muted small" }, "applies to any lane set to “inherit”"));
}

// ---- the overlay editors ---------------------------------------------------------------------

function overlay(title, body, { onClose }) {
  const close = el("button", { class: "btn small ghost", "data-lane-editor-close": "" }, "✕ close");
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
function openLaneCreate(data, { reload }) {
  const nameIn = el("input", { type: "text", placeholder: "lane name",
    "data-lane-new-name": "", "data-nopersist": true, style: "width:220px" });
  const claimed = claimedElsewhere(data);
  const free = data.known_routines.filter((r) => !claimed.has(r.slug));
  const picker = el("select", { multiple: "", size: "6", style: "min-width:240px",
    "data-lane-members": "" },
    ...free.map((r) => el("option", { value: r.slug }, r.name)));
  const ofSel = el("select", { "data-lane-onfailure": "" },
    el("option", { value: "" }, "inherit default"),
    ...data.on_failure_vocab.map((v) => el("option", { value: v }, v)));
  const addBtn = el("button", { class: "btn primary" }, "add lane");
  const body = el("div", { class: "mt" },
    el("div", { class: "muted small", style: "margin-bottom:8px" },
      "A lane runs its routines in order, one after another — it is the only thing that "
      + "decides when they fire. For a flow with an inbound and an outbound end, bracket the "
      + "lane: an inbound-router routine first, an outbound-sender routine last. Fire order "
      + "and the lane schedule are edited on the lane after it is created."),
    el("div", { class: "row", style: "flex-wrap:wrap;gap:10px;align-items:flex-start" },
      el("label", { class: "small" }, el("div", { class: "muted" }, "name"), nameIn),
      el("label", { class: "small" }, el("div", { class: "muted" },
        "members (Ctrl/⌘-click to multi-select)"), picker),
      el("label", { class: "small" }, el("div", { class: "muted" }, "on failure"), ofSel)),
    claimed.size ? el("div", { class: "muted small mt" },
      `${claimed.size} routine${claimed.size === 1 ? " is" : "s are"} not listed — each already `
      + "runs in another lane; a routine belongs to at most one") : null,
    el("div", { class: "row mt" }, addBtn));
  const ov = overlay("New lane", body, { onClose: null });
  addBtn.onclick = async () => {
    const name = nameIn.value.trim();
    if (!name) { toast("a lane name is required"); return; }
    const members = [...picker.selectedOptions].map((o) => ({ slug: o.value }));
    try {
      await api("/api/lanes", { method: "POST",
        body: { name, members, on_failure: ofSel.value || null } });
      toast(`lane “${name}” added`); ov.close(); reload();
    } catch (ex) { err(ex); }
  };
}

/** The per-lane editor: rename, ordered members, add/remove, on-failure, schedule, delete.
 *  Saves apply immediately (each control PATCHes) and the panel re-renders from the response,
 *  so it never goes stale against its own writes. */
export function openLaneEditor(lane, data, { reload }) {
  let l = lane;                      // updated from every PATCH response
  const body = el("div", { class: "mt", "data-lane": l.id });
  const ov = overlay(`Lane “${l.name}”`, body, { onClose: reload });

  const patch = async (fields) => {
    try {
      const r = await api(`/api/lanes/${l.id}`, { method: "PATCH", body: fields });
      l = r.lane;
      render();
    } catch (ex) { err(ex); render(); }
  };
  const saveMembers = () => patch({ members: l.members });

  function render() {
    body.replaceChildren();

    // rename — applies on change (blur/Enter), like every other immediate-save control here
    const nameIn = el("input", { type: "text", value: l.name, "data-lane-name": "",
      "data-nopersist": true, style: "width:220px" });
    nameIn.onchange = () => { if (nameIn.value.trim()) patch({ name: nameIn.value.trim() }); };
    body.append(el("label", { class: "small" },
      el("div", { class: "muted" }, "name"), nameIn));

    // ordered member rows: ↑/↓ reorder, remove
    const rows = el("div", { class: "mt" });
    if (!(l.members || []).length) rows.append(el("div", { class: "muted small" }, "no members"));
    (l.members || []).forEach((m, i) => {
      const up = el("button", { class: "btn small", ...(i === 0 ? { disabled: "" } : {}) }, "↑");
      const down = el("button", { class: "btn small",
        ...(i === l.members.length - 1 ? { disabled: "" } : {}) }, "↓");
      const rm = el("button", { class: "btn small danger" }, "remove");
      up.onclick = () => { [l.members[i - 1], l.members[i]] = [l.members[i], l.members[i - 1]]; saveMembers(); };
      down.onclick = () => { [l.members[i + 1], l.members[i]] = [l.members[i], l.members[i + 1]]; saveMembers(); };
      rm.onclick = () => { l.members.splice(i, 1); saveMembers(); };
      rows.append(el("div", { class: "row", style: "gap:6px;align-items:center",
        "data-member": m.slug },
        el("span", { class: "small mono", style: "width:22px" }, `${i + 1}.`),
        el("span", { class: "small", style: "min-width:160px" }, m.slug),
        up, down, rm));
    });
    body.append(rows);

    // add a member: routines in no lane at all (this lane's own are already listed above;
    // one another lane holds cannot be added — at most one lane per routine)
    const present = new Set((l.members || []).map((m) => m.slug));
    const claimed = claimedElsewhere(data, l.id);
    const addable = data.known_routines.filter((r) => !present.has(r.slug) && !claimed.has(r.slug));
    if (addable.length) {
      const sel = el("select", { "data-lane-add-member": "" },
        ...addable.map((r) => el("option", { value: r.slug }, r.name)));
      const addBtn = el("button", { class: "btn small" }, "add member");
      addBtn.onclick = () => {
        l.members = [...(l.members || []), { slug: sel.value }];
        saveMembers();
      };
      body.append(el("div", { class: "row mt", style: "gap:6px;align-items:center" },
        sel, addBtn));
    }

    // per-lane on-failure override
    const ofSel = el("select", { "data-lane-onfailure": "" },
      el("option", { value: "", ...(l.on_failure ? {} : { selected: "" }) }, "inherit default"),
      ...data.on_failure_vocab.map((v) =>
        el("option", { value: v, ...(l.on_failure === v ? { selected: "" } : {}) }, v)));
    ofSel.onchange = () => patch({ on_failure: ofSel.value || null, set_on_failure: true });
    body.append(el("div", { class: "row mt", style: "gap:8px;align-items:center" },
      el("span", { class: "small" }, "on failure:"), ofSel,
      el("span", { class: "muted small" },
        `inherit = the instance default (${data.default_on_failure})`)));

    // D71: the lane schedule — fires the chain on this cadence; member crons suppressed.
    const sched = scheduleEditor(l.schedule_friendly || { frequency: "manual" }, data.server_tz);
    const schedBtn = el("button", { class: "btn small", "data-lane-schedule-save": "" },
      "save schedule");
    schedBtn.onclick = () => patch({ schedule: { friendly: sched.value() } });
    body.append(el("div", { class: "mt", "data-lane-schedule": "" },
      el("div", { class: "small", style: "font-weight:600" }, "Schedule"),
      el("div", { class: "muted small" },
        "fires the chain on this cadence — members run in order; each member's own "
        + "schedule is suppressed while this is set"),
      sched.node,
      el("div", { class: "row mt" }, schedBtn)));

    // A lane is timing; what members SHARE is a domain, named per routine — so this editor
    // points at that surface rather than carrying a control that would have to write several
    // routines' config from here.
    body.append(el("div", { class: "muted small mt", "data-lane-domain-note": "" },
      "Permissions, rules, secrets, roots and the shared store are a DOMAIN, not a lane. "
      + "A routine names its domain in its own config; the Domains section of the Routines "
      + "page edits what that domain shares."));

    // delete — closes the editor; the dashboard reloads via onClose
    const del = el("button", { class: "btn small danger" }, "delete lane");
    del.onclick = async () => {
      if (!(await confirmDialog(`Delete lane “${l.name}”? Its members go back to their own `
        + "schedules; nothing else about them changes.", { confirmLabel: "delete" }))) return;
      try { await api(`/api/lanes/${l.id}`, { method: "DELETE" });
        toast("lane deleted"); ov.close(); }
      catch (ex) { err(ex); }
    };
    body.append(el("div", { class: "row mt", style: "justify-content:flex-end" }, del));
  }

  render();
}
