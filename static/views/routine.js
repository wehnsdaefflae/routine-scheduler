// Routine detail: schedule, fragment standards (toggle + edit), workflow reference,
// editable instruction / steps / fragment files, state, runs.

import { api } from "/static/api.js";
import { chip, el, fmtTokens, fmtTs, scheduleEditor, tagChip, toast } from "/static/util.js";

export async function render(view, slug) {
  let d, st;
  try { [d, st] = await Promise.all([api(`/api/routines/${slug}`), api("/api/status").catch(() => ({}))]); }
  catch (err) { view.append(el("div", { class: "empty" }, err.message)); return; }
  const llmReady = st.llm_ready !== false;

  const stateChip = d.active_state ? chip(d.active_state, d.active_state)
    : d.enabled ? chip("idle", "idle") : chip("disabled", "disabled");
  view.append(el("div", { class: "page-head" },
    el("h1", {}, d.name || slug),
    el("div", { class: "row" }, stateChip,
      d.active_run
        ? el("a", { class: "btn primary", href: `#/run/${d.active_run}` }, "◉ watch live")
        : el("button", { class: "btn primary", disabled: !llmReady,
            title: llmReady ? "" : "connect an LLM endpoint in Settings first", onclick: runNow }, "▶ run now"),
      el("button", { class: "btn danger", onclick: archive }, "archive"))));
  if (d.problems?.length) {
    view.append(el("div", { class: "panel", style: "border-color:var(--err);margin-top:14px" },
      d.problems.map((p) => el("div", { style: "color:var(--err)" }, `⚠ ${p}`))));
  }

  async function runNow(e) {
    e.target.disabled = true;
    try { const r = await api(`/api/routines/${slug}/run`, { method: "POST" });
      location.hash = `#/run/${r.run_id}`; }
    catch (err) { toast(err.message); e.target.disabled = false; }
  }
  async function archive() {
    if (!confirm(`Archive "${slug}"? It leaves the scheduler (dir moves to .archive).`)) return;
    try { await api(`/api/routines/${slug}/archive`, { method: "POST" }); location.hash = "#/"; }
    catch (err) { toast(err.message); }
  }

  // -- tags -----------------------------------------------------------------------
  let tags = [...(d.tags || [])];
  const tagsRow = el("div", { class: "tags" });
  const tagInput = el("input", { type: "text", placeholder: "add tag…", style: "width:130px" });
  function renderTags() {
    tagsRow.innerHTML = "";
    tags.forEach((t) => tagsRow.append(tagChip(t,
      { onRemove: () => { tags = tags.filter((x) => x !== t); renderTags(); } })));
    tagsRow.append(tagInput);
  }
  function addTag() {
    const v = tagInput.value.trim().toLowerCase().replace(/\s+/g, "-");
    if (v && !tags.includes(v)) { tags.push(v); tagInput.value = ""; renderTags(); }
    tagInput.focus();
  }
  tagInput.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } };
  renderTags();
  view.append(el("h2", {}, "Tags"),
    el("div", { class: "panel" },
      el("div", { class: "muted", style: "font-family:var(--mono);font-size:12px;margin-bottom:8px" },
        "freeform labels for filtering on the Dashboard (e.g. meta tucks a routine away by default)"),
      tagsRow,
      el("div", { class: "row mt" },
        el("button", { class: "btn small", onclick: addTag }, "+ add"),
        el("button", {
          class: "btn primary",
          onclick: async () => {
            try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { tags } }); toast("tags saved"); }
            catch (err) { toast(err.message); }
          },
        }, "save tags"))));

  // -- schedule -------------------------------------------------------------------
  const sched = scheduleEditor(d.schedule_friendly || { frequency: "manual" }, d.server_tz);
  const enabledBox = el("input", { type: "checkbox", checked: d.enabled || null });
  view.append(el("h2", {}, "Schedule"),
    el("div", { class: "panel" }, sched.node,
      el("label", { class: "row mt", style: "gap:8px" }, enabledBox, "enabled"),
      el("div", { class: "row mt" }, el("button", {
        class: "btn primary",
        onclick: async () => {
          try {
            await api(`/api/routines/${slug}`, { method: "PATCH",
              body: { enabled: enabledBox.checked, schedule: { friendly: sched.value() } } });
            toast("schedule saved"); setTimeout(() => location.reload(), 400);
          } catch (err) { toast(err.message); }
        },
      }, "save schedule")),
      d.next_fire ? el("div", { class: "muted mt", style: "font-family:var(--mono);font-size:12px" },
        `next run · ${new Date(d.next_fire).toLocaleString()}`) : null));

  // -- workflow = the routine's OWN main.md (self-contained; generated from a recipe) ----------
  view.append(el("h2", {}, "Workflow (main.md)"),
    el("div", { class: "panel row spread" },
      el("div", {},
        el("span", { class: "ref-tag" }, d.workflow_ref?.slug || "—"),
        el("span", { class: "muted", style: "margin-left:10px;font-size:12.5px" },
          "the recipe this routine was born from — main.md is the routine's OWN now, editable below")),
      el("button", { class: "btn small",
        onclick: () => editFile("main.md", "main.md — the routine's workflow") }, "edit main.md")));

  // -- standards = fragments (toggle + edit) --------------------------------------
  const boxes = {};
  const fragRows = (d.fragments || []).map((f) => {
    const box = el("input", { type: "checkbox", checked: f.active ? "" : null });
    boxes[f.slug] = box;
    const editLink = f.active
      ? el("a", { href: "#", class: "muted", style: "font-size:11.5px;font-family:var(--mono)",
                  onclick: (e) => { e.preventDefault(); editFile(`fragments/${f.slug}.md`, `fragment: ${f.slug}`); } },
          "edit this routine's copy")
      : null;
    return el("label", { class: "toggle-row" }, box,
      el("div", {},
        el("div", { class: "t-title" }, f.slug),
        el("div", { class: "muted", style: "font-size:12.5px" }, f.summary || ""),
        editLink));
  });
  view.append(el("h2", {}, "Standards (fragments)"),
    el("div", { class: "panel" },
      fragRows.length ? fragRows : el("div", { class: "muted" }, "no fragments in the library"),
      el("div", { class: "row mt" }, el("button", {
        class: "btn primary",
        onclick: async () => {
          const active = Object.entries(boxes).filter(([, b]) => b.checked).map(([s]) => s);
          try { await api(`/api/routines/${slug}/fragments`, { method: "PUT", body: { active } });
            toast("standards saved"); setTimeout(() => location.reload(), 400); }
          catch (err) { toast(err.message); }
        },
      }, "save standards"))));

  // -- editable files: instruction + steps -------------------------------------
  view.append(el("h2", {}, "Instruction"));
  view.append(docEditor("Instruction (the task)", d.instruction, async (content) => {
    await api(`/api/routines/${slug}/instruction`, { method: "PUT", body: { content } });
  }));

  const stepFiles = (d.files?.steps) || [];
  view.append(el("h2", {}, "Step modules"),
    el("div", { class: "panel" },
      stepFiles.length
        ? el("div", { class: "row" }, stepFiles.map((n) =>
            el("button", { class: "btn small", onclick: () => editFile(`steps/${n}`, n) }, n)))
        : el("div", { class: "muted" }, "none — this recipe keeps its whole flow in main.md")));

  const fileEditor = el("div", {});
  view.append(fileEditor);

  async function editFile(path, label) {
    let data; try { data = await api(`/api/routines/${slug}/file?path=${encodeURIComponent(path)}`); }
    catch (err) { toast(err.message); return; }
    fileEditor.innerHTML = "";
    const node = docEditor(label, data.content, async (content) => {
      await api(`/api/routines/${slug}/file`, { method: "PUT", body: { path, content } });
    });
    fileEditor.append(node);
    node.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function docEditor(label, content, save) {
    const ta = el("textarea", { class: "code" }, content || "");
    const btn = el("button", { class: "btn primary" }, "save");
    btn.onclick = async () => {
      try { await save(ta.value); toast(`${label} saved`); } catch (err) { toast(err.message, 5000); }
    };
    return el("div", { class: "panel mt" },
      el("div", { class: "muted", style: "font-family:var(--mono);font-size:12px;margin-bottom:8px" }, label),
      ta, el("div", { class: "row mt" }, btn));
  }

  // -- questions ------------------------------------------------------------------
  if (d.questions?.length) {
    view.append(el("h2", {}, "Decisions"),
      el("div", { class: "panel" }, d.questions.map((q) =>
        el("div", { class: "row spread", style: "padding:5px 0" },
          el("span", {}, `❓ ${q.question}`),
          el("a", { class: "btn small", href: "#/questions" }, "answer")))));
  }

  // -- state + ledger -------------------------------------------------------------
  const stateFiles = (d.files?.state) || [];
  view.append(el("h2", {}, "State & memory"),
    el("div", { class: "panel" },
      el("div", { class: "muted", style: "font-family:var(--mono);font-size:12px" },
        stateFiles.length ? `state/ · ${stateFiles.join("  ·  ")}` : "no state files yet"),
      el("details", { class: "mt" }, el("summary", { style: "cursor:pointer" }, "LEDGER tail"),
        el("pre", { class: "doc mt" }, d.ledger_tail || "(empty)"))));

  // -- runs -----------------------------------------------------------------------
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
