// Library: workflows (control-flow patterns), fragments (standards), global utils.
// A tag filter narrows all three sections; each element shows its tags (edit them in its file).
// Deep-link #/library/workflow/<slug> opens an editor directly.

import { api } from "/static/api.js";
import { chip, el, tagChip, toast } from "/static/util.js";

export async function render(view, sub) {
  let data;
  try { data = await api("/api/library"); }
  catch (err) { view.append(el("div", { class: "empty" }, err.message)); return; }

  view.append(el("div", { class: "page-head" },
    el("h1", {}, "Library"),
    el("span", { class: "muted", style: "font-family:var(--mono);font-size:12px" },
      `workflows ${data.workflows.length} · fragments ${data.fragments.length} · utils ${data.utils.length}`)));

  const filterBar = el("div", { class: "filterbar" });
  const sections = el("div", {});
  const editor = el("div", {});
  view.append(filterBar, sections, editor);

  const active = new Set();
  const matches = (tags) => !active.size || (tags || []).some((t) => active.has(t));

  function renderFilterBar() {
    const all = [...new Set([...data.workflows, ...data.fragments, ...data.utils]
      .flatMap((x) => x.tags || []))].sort((a, b) => (a === "meta" ? -1 : b === "meta" ? 1 : a.localeCompare(b)));
    filterBar.innerHTML = "";
    if (!all.length) return;
    filterBar.append(el("span", { class: "lbl" }, "filter"));
    for (const t of all) filterBar.append(tagChip(t, {
      active: active.has(t),
      onClick: () => { active.has(t) ? active.delete(t) : active.add(t); renderFilterBar(); renderSections(); },
    }));
    if (active.size) filterBar.append(el("a", {
      href: "#", class: "muted", style: "font-family:var(--mono);font-size:11px",
      onclick: (e) => { e.preventDefault(); active.clear(); renderFilterBar(); renderSections(); },
    }, "clear"));
  }

  function renderSections() {
    sections.innerHTML = "";
    section("Workflows", "the control-flow patterns routines follow",
      data.workflows.filter((w) => matches(w.tags)).map((w) =>
        item(w.name || w.slug, w.status, w.problems, w.tags, () => openWorkflow(w.slug))));
    section("Fragments", "reusable standards routines toggle on (self-management, tool use)",
      data.fragments.filter((f) => matches(f.tags)).map((f) =>
        item(f.slug, "", f.problems, f.tags, () => openFragment(f.slug), f.summary)));
    section("Global utils", "the tools routines run (created + revised on demand, selftest-gated)",
      data.utils.filter((u) => matches(u.tags)).map((u) =>
        item(u.name, "", [], u.tags, () => openUtil(u.name), u.summary)));
  }

  function section(title, desc, rows) {
    sections.append(el("h2", {}, title));
    sections.append(el("div", { class: "panel", style: "padding:0" },
      el("div", { class: "muted", style: "padding:12px 16px;font-size:12.5px;border-bottom:1px solid var(--line)" }, desc),
      el("table", { class: "list" }, el("tbody", {}, rows.length ? rows
        : el("tr", {}, el("td", { class: "muted" }, active.size ? "none match this filter" : "none"))))));
  }

  function item(label, status, problems, tags, onopen, summary) {
    return el("tr", {},
      el("td", {}, el("a", { href: "#", onclick: (e) => { e.preventDefault(); onopen(); } }, label)),
      el("td", {}, status ? chip(status, status === "stable" ? "ok" : "partial") : ""),
      el("td", {}, (tags || []).length ? el("div", { class: "tags" }, tags.map((t) => tagChip(t))) : ""),
      el("td", { class: "muted", style: "max-width:460px" }, summary || ""),
      el("td", {}, (problems && problems.length) ? chip(`${problems.length} lint`, "failed") : ""));
  }

  async function openWorkflow(slug) {
    const d = await api(`/api/workflows/${slug}`);
    showEditor(`workflow: ${slug}`, d.content, d.log, async (content) =>
      api(`/api/workflows/${slug}`, { method: "PUT", body: { content } }));
  }
  async function openFragment(slug) {
    const d = await api(`/api/library/fragments/${slug}`);
    showEditor(`fragment: ${slug}`, d.content, d.log, async (content) =>
      api(`/api/library/fragments/${slug}`, { method: "PUT", body: { content } }));
  }
  async function openUtil(name) {
    const d = await api(`/api/library/utils/${name}`);
    showEditor(`util: ${name} (selftest-gated)`, d.content, null, async (content) =>
      api(`/api/library/utils/${name}`, { method: "PUT", body: { content } }));
  }

  function showEditor(label, content, log, save) {
    editor.innerHTML = "";
    const ta = el("textarea", { class: "code", style: "min-height:360px" }, content);
    const btn = el("button", { class: "btn primary" }, "save + commit");
    btn.onclick = async () => {
      try { await save(ta.value); toast("saved + committed — reload to see tag changes"); }
      catch (err) { toast(err.message, 6000); }
    };
    editor.append(el("h2", {}, label),
      el("div", { class: "panel" }, ta, el("div", { class: "row mt" }, btn),
        el("div", { class: "muted mt", style: "font-size:11.5px;font-family:var(--mono)" },
          "tags live in this file's frontmatter/header — edit them here"),
        log ? el("details", { class: "mt" }, el("summary", { style: "cursor:pointer" }, "git history"),
          el("table", { class: "list" }, el("tbody", {}, (log || []).map((c) =>
            el("tr", {}, el("td", { class: "mono" }, c.commit), el("td", {}, c.date),
              el("td", { class: "muted" }, c.subject)))))) : null));
    editor.scrollIntoView({ behavior: "smooth" });
  }

  renderFilterBar();
  renderSections();

  // deep-link: #/library/workflow/<slug>
  if (sub) {
    const [kind, id] = sub.split("/");
    if (kind === "workflow" && id) openWorkflow(id).catch((e) => toast(e.message));
    if (kind === "fragment" && id) openFragment(id).catch((e) => toast(e.message));
    if (kind === "util" && id) openUtil(id).catch((e) => toast(e.message));
  }

  // proposals (from the meta routine)
  const proposals = await api("/api/proposals").catch(() => []);
  if (proposals.length) {
    view.append(el("h2", {}, "Proposals (from the meta routine)"));
    for (const p of proposals) {
      const note = el("input", { type: "text", placeholder: "optional note…", style: "flex:1" });
      const decide = (decision) => el("button",
        { class: `btn small ${decision === "accepted" ? "primary" : "danger"}`,
          onclick: async () => {
            try { await api(`/api/proposals/${p.id}/decide`, { method: "POST", body: { decision, note: note.value } });
              toast(`proposal ${decision}`); location.reload(); }
            catch (err) { toast(err.message); } } }, decision);
      view.append(el("div", { class: "panel mt" },
        el("div", { class: "row spread" }, el("strong", {}, p.id),
          p.decision ? chip(p.decision.decision, p.decision.decision === "accepted" ? "ok" : "failed") : null),
        el("pre", { class: "doc mt" }, p.content),
        p.decision ? null : el("div", { class: "row mt" }, note, decide("accepted"), decide("declined"))));
    }
  }
}
