// Library: workflows (control-flow patterns), traits (practice prose), permissions (grants), global utils.
// A tag filter narrows all three sections; deep-link #/library/workflow/<slug> opens an
// editor directly. Save failures (lint / selftest) render inline under the editor;
// decisions update in place — no page reloads.

import { api } from "/static/api.js";
import { codeEditor } from "/static/components/code.js";
import { replaceHash } from "/static/router.js";
import { chip, el, emptyState, skeleton, tagChip, toast, when } from "/static/util.js";

export async function render(view, sub, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / library"),
      el("h1", {}, "Library"))));
  const countLine = el("div", { class: "sub muted" });
  const filterBar = el("div", { class: "filterbar" });
  const sections = el("div", {});
  const editor = el("div", {});
  sections.append(skeleton());
  view.append(countLine, filterBar, sections, editor);

  let data;
  try { data = await api("/api/library"); }
  catch (err) { sections.replaceChildren(emptyState("✕", "Couldn't load the library", err.message)); return; }
  countLine.textContent =
    `workflows ${data.workflows.length} · traits ${data.traits.length} · permissions ${data.permissions.length} · utils ${data.utils.length}`;

  // Both the tag filter and the open editor are kept in the URL (#/library/<kind>/<slug>?tags=…)
  // so the view is shareable and restores on reload — without tearing itself down on each change.
  let openSub = sub || null;
  const active = new Set((query.tags || "").split(",").filter(Boolean));
  const updateURL = () => replaceHash(openSub ? `#/library/${openSub}` : "#/library",
    { tags: [...active].join(",") });
  const matches = (tags) => !active.size || (tags || []).some((t) => active.has(t));

  function renderFilterBar() {
    const all = [...new Set([...data.workflows, ...data.traits, ...data.permissions, ...data.utils]
      .flatMap((x) => x.tags || []))].sort((a, b) => (a === "meta" ? -1 : b === "meta" ? 1 : a.localeCompare(b)));
    filterBar.replaceChildren();
    if (!all.length) return;
    filterBar.append(el("span", { class: "lbl" }, "filter"));
    for (const t of all) filterBar.append(tagChip(t, {
      active: active.has(t),
      onClick: () => { active.has(t) ? active.delete(t) : active.add(t); updateURL(); renderFilterBar(); renderSections(); },
    }));
    if (active.size) filterBar.append(el("button", { class: "btn ghost small",
      onclick: () => { active.clear(); updateURL(); renderFilterBar(); renderSections(); } }, "clear"));
  }

  function renderSections() {
    sections.replaceChildren();
    section("Workflows", "the control-flow patterns routines follow",
      data.workflows.filter((w) => matches(w.tags)).map((w) =>
        item(w.name || w.slug, w.status, w.problems, w.tags, () => openWorkflow(w.slug))));
    section("Traits", "reusable practices — adapted into each new routine at creation, then owned by the routine (this is only the template)",
      data.traits.filter((f) => matches(f.tags)).map((f) =>
        item(f.slug, "", f.problems, f.tags, () => openDoc("traits", f.slug), f.summary)));
    section("Permissions", "engine-enforced capabilities — held per routine via its Permissions panel; the grants: frontmatter here is the machine-read authority",
      data.permissions.filter((f) => matches(f.tags)).map((f) =>
        item(f.slug, "", f.problems, f.tags, () => openDoc("permissions", f.slug), f.summary)));
    section("Global utils", "the tools routines run (created + revised on demand, selftest-gated)",
      data.utils.filter((u) => matches(u.tags)).map((u) =>
        item(u.name, "", [], u.tags, () => openUtil(u.name), u.summary)));
  }

  function section(title, desc, rows) {
    sections.append(el("h2", {}, title));
    sections.append(el("div", { class: "panel", style: "padding:0" },
      el("div", { class: "muted small", style: "padding:11px 16px;border-bottom:1px solid var(--line)" }, desc),
      el("div", { class: "tablewrap" },
        el("table", { class: "list" }, el("tbody", {}, rows.length ? rows
          : el("tr", {}, el("td", { class: "muted" }, active.size ? "none match this filter" : "none")))))));
  }

  function item(label, status, problems, tags, onopen, summary) {
    return el("tr", {},
      el("td", {}, el("a", { href: "#", onclick: (e) => { e.preventDefault(); onopen(); } }, label)),
      el("td", {}, status ? chip(status, status === "stable" ? "ok" : "partial") : ""),
      el("td", {}, (tags || []).length ? el("div", { class: "tags" }, tags.map((t) => tagChip(t))) : ""),
      el("td", { class: "muted prose", style: "max-width:460px" }, summary || ""),
      el("td", {}, (problems && problems.length)
        ? el("span", { class: "chip failed", title: problems.join("\n") }, `${problems.length} lint`) : ""));
  }

  async function openWorkflow(slug) {
    openSub = `workflow/${slug}`; updateURL();
    const d = await api(`/api/workflows/${slug}`);
    showEditor(`workflow: ${slug}`, d.content, d.log, async (content) =>
      api(`/api/workflows/${slug}`, { method: "PUT", body: { content } }), "python",
      async () => {
        if (!confirm(`Delete workflow "${slug}"? Routines born from it keep their own `
                     + "recipes. A seed pattern returns at the next daemon boot.")) return false;
        await api(`/api/workflows/${slug}`, { method: "DELETE" });
        return true;
      });
  }
  async function openDoc(kind, slug) {
    openSub = `${kind.slice(0, -1)}/${slug}`; updateURL();
    const d = await api(`/api/library/${kind}/${slug}`);
    showEditor(`${kind.slice(0, -1)}: ${slug}`, d.content, d.log, async (content) =>
      api(`/api/library/${kind}/${slug}`, { method: "PUT", body: { content } }));
  }
  async function openUtil(name) {
    openSub = `util/${name}`; updateURL();
    const d = await api(`/api/library/utils/${name}`);
    showEditor(`util: ${name} (selftest-gated)`, d.content, null, async (content) =>
      api(`/api/library/utils/${name}`, { method: "PUT", body: { content } }), "python");
  }

  // workflows + utils are Python → highlighted editor; traits/permissions are markdown → plain
  function showEditor(label, content, log, save, lang, del) {
    editor.replaceChildren();
    const ed = codeEditor(content, { lang, minHeight: 360 });
    const errBox = el("div", {});
    const delBtn = !del ? null : el("button", { class: "btn danger small" }, "delete");
    if (delBtn) {
      delBtn.onclick = async () => {
        delBtn.disabled = true;
        try {
          if (await del()) { toast("deleted + committed"); location.reload(); return; }
        } catch (err) { toast(err.message, 5000, { error: true }); }
        delBtn.disabled = false;
      };
    }
    const btn = el("button", { class: "btn primary" }, "save + commit");
    btn.onclick = async () => {
      btn.disabled = true;
      errBox.replaceChildren();
      try { await save(ed.value); toast("saved + committed — reload to see tag changes"); }
      catch (err) {
        // lint / selftest output arrives as the error detail — show it AT the editor, in full
        errBox.append(el("div", { class: "save-errors" },
          el("strong", {}, "not saved — the gate rejected it:\n"), err.message));
        toast("save rejected — details below the editor", 3500, { error: true });
      }
      finally { btn.disabled = false; }
    };
    editor.append(el("h2", {}, label),
      el("div", { class: "panel" }, ed.node,
        el("div", { class: "row mt" }, btn, delBtn),
        errBox,
        el("div", { class: "muted mt small" },
          "tags live in this file's frontmatter/header — edit them here"),
        log ? el("details", { class: "mt" }, el("summary", { style: "cursor:pointer" }, "git history"),
          el("div", { class: "tablewrap" },
            el("table", { class: "list" }, el("tbody", {}, (log || []).map((c) =>
              el("tr", {}, el("td", {}, c.commit), el("td", {}, c.date),
                el("td", { class: "muted" }, c.subject))))))) : null));
    editor.scrollIntoView({ behavior: "smooth" });
  }

  renderFilterBar();
  renderSections();

  // deep-link: #/library/workflow/<slug>
  if (sub) {
    const [kind, id] = sub.split("/");
    const opener = { workflow: openWorkflow,
                     trait: (id) => openDoc("traits", id),
                     permission: (id) => openDoc("permissions", id),
                     util: openUtil }[kind];
    if (opener && id) opener(id).catch((e) => toast(e.message, 4000, { error: true }));
  }

}
