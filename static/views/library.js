// Library: workflows (control-flow patterns), fragments (standards), global utils.
// Deep-link #/library/workflow/<slug> opens a workflow editor directly.

import { api } from "/static/api.js";
import { chip, el, toast } from "/static/util.js";

export async function render(view, sub) {
  let data;
  try { data = await api("/api/library"); }
  catch (err) { view.append(el("div", { class: "empty" }, err.message)); return; }

  view.append(el("div", { class: "page-head" },
    el("h1", {}, "Library"),
    el("span", { class: "muted", style: "font-family:var(--mono);font-size:12px" },
      `workflows ${data.workflows.length} · fragments ${data.fragments.length} · utils ${data.utils.length}`)));

  const editor = el("div", {});

  section("Workflows", "the control-flow patterns routines follow",
    data.workflows.map((w) => item(w.name || w.slug, w.slug, w.status, w.problems,
      () => openWorkflow(w.slug))));
  section("Fragments", "reusable standards routines toggle on (self-management, tool use)",
    data.fragments.map((f) => item(f.slug, f.slug, "", f.problems,
      () => openFragment(f.slug), f.summary)));
  section("Global utils", "the tools routines run (created + revised on demand, selftest-gated)",
    data.utils.map((u) => item(u.name, u.name, "", [],
      () => openUtil(u.name), u.summary)));

  view.append(editor);

  function section(title, desc, rows) {
    view.append(el("h2", {}, title));
    view.append(el("div", { class: "panel", style: "padding:0" },
      el("div", { class: "muted", style: "padding:12px 16px;font-size:12.5px;border-bottom:1px solid var(--line)" }, desc),
      el("table", { class: "list" }, el("tbody", {}, rows.length ? rows
        : el("tr", {}, el("td", { class: "muted" }, "none"))))));
  }

  function item(label, id, status, problems, onopen, summary) {
    return el("tr", {},
      el("td", {}, el("a", { href: "#", onclick: (e) => { e.preventDefault(); onopen(); } }, label)),
      el("td", {}, status ? chip(status, status === "stable" ? "ok" : "partial") : ""),
      el("td", { class: "muted", style: "max-width:520px" }, summary || ""),
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
      try { await save(ta.value); toast("saved + committed"); }
      catch (err) { toast(err.message, 6000); }
    };
    editor.append(el("h2", {}, label),
      el("div", { class: "panel" }, ta, el("div", { class: "row mt" }, btn),
        log ? el("details", { class: "mt" }, el("summary", { style: "cursor:pointer" }, "git history"),
          el("table", { class: "list" }, el("tbody", {}, (log || []).map((c) =>
            el("tr", {}, el("td", { class: "mono" }, c.commit), el("td", {}, c.date),
              el("td", { class: "muted" }, c.subject)))))) : null));
    editor.scrollIntoView({ behavior: "smooth" });
  }

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
