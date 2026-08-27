// The GOAL panel (F334/D98) — the user's meaning-level bounds on a job, and how far the run
// has got through them.
//
// This is the half that was missing. D98=A specified this panel, 0.208.0 deferred it into
// F324's shared-rail build, and F324 shipped the rail and closed without it — so the feature
// was enforced in the prompt and the finish gate while being completely invisible, and its
// status column was frozen at `open` because nothing wrote a verdict back either. Both halves
// land together, because a badge over a store nothing updates would just lie more legibly.
//
// The panel shows the STRUCTURE, not a checklist: a group carries ALL/ANY, the document
// carries ALL/ANY over the groups, and a condition waiting on another says so. A flat list of
// ticks cannot express "either of these two ends the job", which is the whole reason the
// conditions are logically connected in the first place.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, toast } from "/static/util.js";

const MARK = { met: "✓", dropped: "–", open: "○" };
const NEXT_STATUS = { open: "met", met: "dropped", dropped: "open" };

export function createStopping(mount, { url, showStage = false, onVerdict } = {}) {
  const body = el("div", { class: "goals" });
  mount.append(body);
  let doc = null;             // the live document, edited in place then PUT whole
  let dirty = false;

  const gid = () => {
    let n = 1;
    while (doc.groups.some((g) => g.id === `g${n}`)) n += 1;
    return `g${n}`;
  };
  const cid = () => {
    let n = 1;
    while (doc.conditions.some((c) => c.id === `s${n}`)) n += 1;
    return `s${n}`;
  };

  function markDirty() { dirty = true; paint(); }

  async function save() {
    try {
      const sent = await api(url, { method: "PUT", body: {
        mode: doc.mode,
        groups: doc.groups.map((g) => ({ id: g.id, name: g.name, mode: g.mode })),
        // `blocked` and the verdict are DERIVED — sending them back would invite the server
        // and the panel to disagree about which one is authoritative
        conditions: doc.conditions.map((c) => ({
          id: c.id, text: c.text, status: c.status, group: c.group,
          requires: c.requires || [], stage: c.stage || "" })),
      } });
      doc = sent;
      dirty = false;
      paint();
      toast("goal saved — it applies from the next run");
    } catch (err) { toast(err.message, 5000, { error: true }); }
  }

  // ---- rendering -------------------------------------------------------------------------

  function verdictChip() {
    const v = doc.verdict || {};
    if (v.satisfied === null || v.satisfied === undefined) return null;
    return el("span", { class: `goal-verdict ${v.satisfied ? "met" : "open"}`,
      title: v.satisfied ? "every condition the goal needs is met"
        : "the goal is not satisfied yet" },
      v.satisfied ? "goal met" : "in progress");
  }

  function conditionRow(c) {
    const blocked = !!c.blocked && c.status === "open";
    // the mark IS the status control: click cycles open → met → dropped. A user who has
    // watched a run conclude something can correct it here without a form.
    const mark = el("button", { class: "goal-mark", title: `${c.status} — click to change` },
      MARK[c.status] || "○");
    mark.onclick = () => { c.status = NEXT_STATUS[c.status] || "open"; markDirty(); };

    const text = el("span", { class: "goal-text", contenteditable: "plaintext-only",
      spellcheck: "false" }, c.text);
    text.onblur = () => {
      const t = text.textContent.trim();
      if (t && t !== c.text) { c.text = t; markDirty(); }
      else text.textContent = c.text;
    };

    const meta = el("span", { class: "goal-meta faint" }, `[${c.id}]`);
    if (blocked) meta.append(` · ${c.blocked}`);
    if (c.stage) meta.append(` · stage ${c.stage}`);
    if (c.note) meta.append(el("span", { class: "goal-note", title: c.note }, ` · ${c.note}`));
    // v2: the verifier objected and the run re-asserted anyway. The verdict stands — the model
    // keeps the last word — but the disagreement is the operator's to judge, so it is visible
    // rather than buried in the store.
    if (c.disputed) {
      meta.append(el("span", { class: "goal-disputed",
        title: `a check of the run's transcript disagreed: ${c.disputed}` }, " · disputed"));
    }

    const del = el("button", { class: "goal-del", title: "remove this condition" }, "✕");
    del.onclick = async () => {
      if (!(await confirmDialog(`Remove "${c.text}"?`, { confirmLabel: "remove" }))) return;
      doc.conditions = doc.conditions.filter((x) => x !== c);
      // a `requires` pointing at a removed condition would read as blocked forever
      for (const other of doc.conditions) {
        other.requires = (other.requires || []).filter((r) => r !== c.id);
      }
      markDirty();
    };
    return el("div", { class: `goal-row ${c.status}${blocked ? " blocked" : ""}` },
      mark, text, meta, requiresPicker(c), showStage ? stageInput(c) : null, del);
  }

  function requiresPicker(c) {
    const sel = el("select", { class: "goal-req small",
      title: "wait until this other condition is met (a dependency, not a check)" });
    sel.append(el("option", { value: "" }, "—"));
    for (const other of doc.conditions) {
      if (other.id === c.id) continue;
      sel.append(el("option", { value: other.id }, `after ${other.id}`));
    }
    sel.value = (c.requires || [])[0] || "";
    sel.onchange = () => { c.requires = sel.value ? [sel.value] : []; markDirty(); };
    return sel;
  }

  function stageInput(c) {
    const inp = el("input", { class: "goal-stage small", placeholder: "any stage",
      value: c.stage || "", title: "live only while the run is in this stage module" });
    inp.onchange = () => { c.stage = inp.value.trim(); markDirty(); };
    return inp;
  }

  function groupBlock(g) {
    const members = doc.conditions.filter((c) => c.group === g.id);
    const v = (doc.verdict?.groups || []).find((x) => x.id === g.id) || {};
    const name = el("input", { class: "goal-gname small", value: g.name,
      placeholder: "group name" });
    name.onchange = () => { g.name = name.value.trim(); markDirty(); };

    const mode = el("button", { class: `goal-mode ${g.mode}`,
      title: "ALL = every condition must be met · ANY = one is enough" }, g.mode.toUpperCase());
    mode.onclick = () => { g.mode = g.mode === "all" ? "any" : "all"; markDirty(); };

    const count = el("span", { class: "faint small" },
      v.total ? `${v.met}/${v.total}` : "empty");
    const add = el("button", { class: "btn small" }, "+ condition");
    add.onclick = () => {
      doc.conditions.push({ id: cid(), text: "new condition", status: "open",
        group: g.id, requires: [], stage: "", note: "", blocked: "" });
      markDirty();
    };
    const del = el("button", { class: "goal-del", title: "remove this group" }, "✕");
    del.onclick = async () => {
      if (members.length && !(await confirmDialog(
        `Remove the group "${g.name || g.id}" and its ${members.length} condition(s)?`,
        { confirmLabel: "remove" }))) return;
      doc.groups = doc.groups.filter((x) => x !== g);
      doc.conditions = doc.conditions.filter((c) => c.group !== g.id);
      markDirty();
    };
    return el("div", { class: `goal-group ${v.satisfied ? "sat" : ""}` },
      el("div", { class: "goal-ghead" }, mode, name, count, add, del),
      ...members.map(conditionRow));
  }

  function paint() {
    body.replaceChildren();
    if (!doc) { body.append(el("div", { class: "faint small" }, "…")); return; }
    const rootMode = el("button", { class: `goal-mode ${doc.mode}`,
      title: "how the GROUPS combine" }, doc.mode.toUpperCase());
    rootMode.onclick = () => { doc.mode = doc.mode === "all" ? "any" : "all"; markDirty(); };
    const head = el("div", { class: "goal-head" },
      el("span", { class: "faint small" }, "done when"), rootMode,
      el("span", { class: "faint small" }, "of:"), verdictChip());
    // the root combiner only means something with more than one group — showing it over a
    // single group is a control that cannot change anything
    if (doc.groups.length < 2) { rootMode.hidden = true; head.firstChild.hidden = true; }
    body.append(head, ...doc.groups.map(groupBlock));

    const addGroup = el("button", { class: "btn small" }, "+ group");
    addGroup.onclick = () => {
      doc.groups.push({ id: gid(), name: "", mode: "all" });
      markDirty();
    };
    const saveBtn = el("button", { class: "btn small primary", hidden: !dirty }, "save goal");
    saveBtn.onclick = save;
    body.append(el("div", { class: "row mt", style: "gap:6px" }, addGroup, saveBtn));
    if (!doc.conditions.length) {
      body.append(el("div", { class: "faint small mt" },
        "No stopping conditions. Without them a run is bounded only by its budgets, which "
        + "are a runaway backstop rather than a definition of done."));
    }
    onVerdict?.(doc.verdict);
  }

  async function refresh() {
    // never clobber edits in flight — a poll landing mid-edit would silently discard them
    if (dirty) return;
    try { doc = await api(url); } catch { return; }
    // a document with no groups still needs one to hang a condition on
    if (!doc.groups.length) doc.groups = [{ id: "g1", name: "", mode: "all" }];
    paint();
  }

  refresh();
  return { refresh, node: body };
}
