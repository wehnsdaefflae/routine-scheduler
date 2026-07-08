// Routine detail: docs, schedule + toggles editor, state files, LEDGER, runs list.

import { api } from "/static/api.js";
import { chip, el, fmtTokens, fmtTs, toast } from "/static/util.js";

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

  // -- schedule + toggles ---------------------------------------------------------
  const cronInput = el("input", { type: "text", value: d.cron || "" });
  const tzInput = el("input", { type: "text", value: d.tz || "Europe/Berlin" });
  const enabledBox = el("input", { type: "checkbox", checked: d.enabled || null });
  const selfBoxes = Object.fromEntries(Object.entries(d.self).map(([k, v]) =>
    [k, el("input", { type: "checkbox", checked: v || null })]));
  view.append(el("h2", {}, "Schedule & standards"),
    el("div", { class: "panel" },
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "cron"), cronInput),
        el("label", { class: "field" }, el("span", {}, "timezone"), tzInput),
        el("label", { class: "field" }, el("span", {}, "enabled"), enabledBox)),
      el("div", { class: "row" },
        Object.entries(selfBoxes).map(([k, box]) =>
          el("label", { class: "row", style: "gap:4px" }, box, k)),
        el("button", {
          class: "btn primary", style: "margin-left:auto",
          onclick: async (e) => {
            try {
              await api(`/api/routines/${slug}`, { method: "PATCH", body: {
                enabled: enabledBox.checked,
                schedule: { cron: cronInput.value.trim(), tz: tzInput.value.trim() },
                self: Object.fromEntries(Object.entries(selfBoxes).map(([k, b]) => [k, b.checked])),
              }});
              toast("saved");
            } catch (err) { toast(err.message); }
          },
        }, "save")),
      d.next_fire ? el("div", { class: "muted mt" }, `next fire: ${new Date(d.next_fire).toLocaleString()}`) : null));

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
