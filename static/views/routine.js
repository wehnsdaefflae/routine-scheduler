// Routine detail: docs, schedule + toggles editor, state files, LEDGER, runs list.

import { api } from "/static/api.js";
import { chip, el, fmtTokens, fmtTs, scheduleEditor, toast, TOGGLE_INFO } from "/static/util.js";

export async function render(view, slug) {
  let d;
  try { d = await api(`/api/routines/${slug}`); }
  catch (err) { view.append(el("div", { class: "empty" }, err.message)); return; }

  const head = el("div", { class: "row spread" },
    el("h1", {}, d.name || slug, " ",
      d.active_state ? chip(d.active_state, d.active_state) : d.enabled ? chip("idle") : chip("disabled", "disabled")),
    el("div", { class: "row" },
      d.active_run
        ? el("a", { class: "btn primary", href: `#/run/${d.active_run}` }, "watch live")
        : el("button", { class: "btn primary", onclick: runNow }, "▶ run now"),
      el("button", { class: "btn danger", onclick: archive }, "archive")));
  view.append(head);
  if (d.problems?.length) {
    view.append(el("div", { class: "panel", style: "border-color:var(--err)" },
      d.problems.map((p) => el("div", { style: "color:var(--err)" }, `⚠ ${p}`))));
  }

  async function runNow(e) {
    e.target.disabled = true;
    try {
      const r = await api(`/api/routines/${slug}/run`, { method: "POST" });
      location.hash = `#/run/${r.run_id}`;
    } catch (err) { toast(err.message); e.target.disabled = false; }
  }
  async function archive() {
    if (!confirm(`Archive routine "${slug}"? It disappears from the scheduler (dir moves to .archive).`)) return;
    try { await api(`/api/routines/${slug}/archive`, { method: "POST" }); location.hash = "#/"; }
    catch (err) { toast(err.message); }
  }

  // -- schedule -------------------------------------------------------------------
  const sched = scheduleEditor(d.schedule_friendly || { frequency: "manual" }, d.server_tz);
  const enabledBox = el("input", { type: "checkbox", checked: d.enabled || null });
  view.append(el("h2", {}, "Schedule"),
    el("div", { class: "panel" },
      sched.node,
      el("label", { class: "row mt", style: "gap:6px" }, enabledBox, "enabled"),
      el("div", { class: "row mt" },
        el("button", {
          class: "btn primary",
          onclick: async (e) => {
            try {
              await api(`/api/routines/${slug}`, { method: "PATCH", body: {
                enabled: enabledBox.checked, schedule: { friendly: sched.value() },
              }});
              toast("schedule saved");
              setTimeout(() => location.reload(), 400);
            } catch (err) { toast(err.message); }
          },
        }, "save schedule")),
      d.next_fire ? el("div", { class: "muted mt" }, `next run: ${new Date(d.next_fire).toLocaleString()}`) : null));

  // -- standards (described toggles) ----------------------------------------------
  const toggles = { ...d.self, confirm_util_changes: d.confirm_util_changes };
  const boxes = {};
  const toggleRows = Object.entries(TOGGLE_INFO).map(([key, [title, desc]]) => {
    const box = el("input", { type: "checkbox", checked: toggles[key] ? "" : null });
    boxes[key] = box;
    return el("label", { class: "toggle-row" }, box,
      el("div", {}, el("div", { style: "font-weight:600" }, title),
        el("div", { class: "muted", style: "font-size:12.5px" }, desc)));
  });
  view.append(el("h2", {}, "Self-management standards"),
    el("div", { class: "panel" }, toggleRows,
      el("div", { class: "row mt" }, el("button", {
        class: "btn primary",
        onclick: async () => {
          const self = {}; ["audit", "improve", "ledger", "fresh_eyes", "hygiene"].forEach(
            (k) => (self[k] = boxes[k].checked));
          try {
            await api(`/api/routines/${slug}`, { method: "PATCH", body: {
              self, confirm_util_changes: boxes.confirm_util_changes.checked,
            }});
            toast("standards saved");
          } catch (err) { toast(err.message); }
        },
      }, "save standards"))));

  // -- docs -----------------------------------------------------------------------
  view.append(el("h2", {}, "Instruction & workflow"));
  view.append(docEditor("instruction", d.instruction), docEditor("workflow", d.workflow));

  function docEditor(kind, content) {
    const ta = el("textarea", { class: "code" }, content);
    const save = el("button", { class: "btn" }, `save ${kind}`);
    save.onclick = async () => {
      try {
        await api(`/api/routines/${slug}/${kind}`, { method: "PUT", body: { content: ta.value } });
        toast(`${kind} saved + committed`);
      } catch (err) { toast(err.message); }
    };
    return el("details", { class: "panel mt", open: kind === "instruction" ? "" : null },
      el("summary", { style: "cursor:pointer;font-weight:600" }, `${kind}.md`),
      el("div", { class: "mt" }, ta, el("div", { class: "row mt" }, save)));
  }

  // -- questions ------------------------------------------------------------------
  if (d.questions?.length) {
    view.append(el("h2", {}, "Open questions"),
      el("div", { class: "panel" }, d.questions.map((q) =>
        el("div", { class: "row spread", style: "padding:4px 0" },
          el("span", {}, `❓ ${q.question}`),
          el("a", { class: "btn small", href: "#/questions" }, "answer")))));
  }

  // -- state files + ledger ---------------------------------------------------------
  const fileList = [];
  for (const [sub, files] of Object.entries(d.files || {})) {
    for (const f of files) fileList.push(`${sub}/${f.name} (${f.size}B)`);
  }
  view.append(el("h2", {}, "State"),
    el("div", { class: "panel" },
      el("div", { class: "muted" }, fileList.length ? fileList.join(" · ") : "no state files yet"),
      el("details", { class: "mt" },
        el("summary", { style: "cursor:pointer" }, "LEDGER tail"),
        el("pre", { class: "doc" }, d.ledger_tail || "(empty)"))));

  // -- runs -------------------------------------------------------------------------
  view.append(el("h2", {}, "Runs"));
  const rows = (d.runs || []).map((r) => el("tr", {},
    el("td", {}, el("a", { href: `#/run/${r.run_id}` }, fmtTs(r.ts))),
    el("td", {}, chip(r.state, r.state)),
    el("td", {}, String(r.turn ?? "")),
    el("td", { class: "muted" }, fmtTokens(r.usage)),
    el("td", { class: "muted", style: "max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" },
      r.summary || "")));
  view.append(el("div", { class: "panel", style: "padding:0" },
    el("table", { class: "list" },
      el("thead", {}, el("tr", {}, ["when", "state", "turns", "tokens", "summary"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.length ? rows : el("tr", {}, el("td", { class: "muted", colspan: 5 }, "no runs yet"))))));
}
