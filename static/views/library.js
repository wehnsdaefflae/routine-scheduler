// Library: workflows (control-flow patterns), traits (practice prose), permissions (grants), global utils.
// A tag filter narrows all three sections; deep-link #/library/workflow/<slug> opens an
// editor directly. Save failures (lint / selftest) render inline under the editor;
// decisions update in place — no page reloads.

import { api } from "/static/api.js";
import { codeEditor } from "/static/components/code.js";
import { replaceHash } from "/static/router.js";
import { el, emptyState, requiresSummary, skeleton, tagChip, toast, when } from "/static/util.js";

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
  data.playbooks = data.playbooks || [];
  countLine.textContent =
    `workflows ${data.workflows.length} · traits ${data.traits.length} · permissions ${data.permissions.length} · playbooks ${data.playbooks.length} · utils ${data.utils.length}`;

  // Both the tag filter and the open editor are kept in the URL (#/library/<kind>/<slug>?tags=…)
  // so the view is shareable and restores on reload — without tearing itself down on each change.
  let openSub = sub || null;
  const active = new Set((query.tags || "").split(",").filter(Boolean));
  const updateURL = () => replaceHash(openSub ? `#/library/${openSub}` : "#/library",
    { tags: [...active].join(",") });
  const matches = (tags) => !active.size || (tags || []).some((t) => active.has(t));

  function renderFilterBar() {
    const all = [...new Set([...data.workflows, ...data.traits, ...data.permissions,
      ...data.playbooks, ...data.utils]
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
        item(w.name || w.slug, w.problems, w.tags, () => openWorkflow(w.slug), w.description)));
    section("Traits", "reusable practices — adapted into each new routine at creation, then owned by the routine (this is only the template)",
      data.traits.filter((f) => matches(f.tags)).map((f) =>
        item(f.slug, f.problems, f.tags, () => openDoc("traits", f.slug), f.summary)),
      el("button", { class: "btn ghost small", onclick: () => newDoc("traits") }, "+ new trait"));
    section("Permissions", "conduct docs — held per routine via its Permissions panel; the requires: frontmatter names the capabilities each doc's instructions presume (activating the doc switches them on; open a doc to edit the mapping)",
      data.permissions.filter((f) => matches(f.tags)).map((f) => {
        const req = requiresSummary(f.requires);
        const summary = req
          ? el("span", {}, f.summary || "",
              el("span", { style: "color:var(--warn)" }, ` ▸ ${req}`))
          : f.summary;
        return item(f.slug, f.problems, f.tags, () => openDoc("permissions", f.slug), summary);
      }),
      el("button", { class: "btn ghost small", onclick: () => newDoc("permissions") }, "+ new permission"));
    section("Playbooks", "one-shot recipes — saved from a conversation (Save as playbook) and reused to seed a new one; MAIN.md is the always-loaded brief",
      data.playbooks.filter((p) => matches(p.tags)).map((p) =>
        item(p.title || p.slug, p.problems, p.tags, () => openPlaybook(p.slug), p.summary)));
    section("Global utils", "the tools routines run (created + revised on demand, selftest-gated)",
      data.utils.filter((u) => matches(u.tags)).map((u) =>
        item(u.name, [], u.tags, () => openUtil(u.name), u.summary)));
  }

  function section(title, desc, rows, action) {
    sections.append(el("h2", {}, title));
    sections.append(el("div", { class: "panel", style: "padding:0" },
      el("div", { class: "muted small",
        style: "padding:11px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:12px" },
        el("span", {}, desc), action || ""),
      el("div", { class: "tablewrap" },
        el("table", { class: "list" }, el("tbody", {}, rows.length ? rows
          : el("tr", {}, el("td", { class: "muted" }, active.size ? "none match this filter" : "none")))))));
  }

  function item(label, problems, tags, onopen, summary) {
    return el("tr", {},
      el("td", {}, el("a", { href: "#", onclick: (e) => { e.preventDefault(); onopen(); } }, label)),
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
    // permissions get a structured, prefilled requires: panel — it is authoritative for
    // that key on save (the server merges it into the frontmatter); prose stays in the editor
    const requires = kind === "permissions" ? requiresPanel(d.requires || {}) : null;
    showEditor(`${kind.slice(0, -1)}: ${slug}`, d.content, d.log, async (content) =>
      api(`/api/library/${kind}/${slug}`, { method: "PUT",
        body: { content, ...(requires ? { requires: requires.value() } : {}) } }),
      undefined, undefined, requires?.node);
  }

  // Author a fresh trait/permission doc: a lint-satisfying template plus a slug field; save
  // PUTs to /api/library/<kind>/<slug> (create-or-update, lint-gated) and reopens the saved doc.
  function newDoc(kind) {
    const isPerm = kind === "permissions";
    const slugIn = el("input", { placeholder: "kebab-case-slug", style: "width:240px" });
    const requires = isPerm ? requiresPanel({}) : null;
    const template = isPerm
      ? "---\ntags: [conduct, capability, draft]\nrequires: {}\n---\n"
        + "# permission: <name> — <one-line summary of the conduct>\n\n"
        + "Short conduct instructions — at most ~14 lines reach the prompt while the doc is held.\n"
        + "Tick what the instructions presume in the requires panel above.\n"
      : "---\ntags: [conduct, practice, draft]\n---\n"
        + "# trait: <name> — <one-line summary of the practice>\n\n"
        + "The practice prose: when it applies, what it looks like in action, what to avoid.\n"
        + "It is adapted to each new routine at creation — write the general form here.\n";
    const head = el("div", { class: "panel", style: "margin-bottom:10px" },
      el("div", { class: "lbl" }, "slug — the doc's file name in the library"), slugIn);
    showEditor(`new ${kind.slice(0, -1)}`, template, null, async (content) => {
      const slug = slugIn.value.trim();
      if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug)) {
        throw new Error("slug must be kebab-case (a-z, 0-9, dashes)");
      }
      await api(`/api/library/${kind}/${slug}`, { method: "PUT",
        body: { content, ...(requires ? { requires: requires.value() } : {}) } });
      await openDoc(kind, slug);   // reopen as the saved doc: URL, git history, panel state
    }, undefined, undefined, requires ? el("div", {}, head, requires.node) : head);
  }

  // The capabilities a permission doc's instructions presume. Prefilled from the doc's
  // frontmatter; edits here win over hand-edited requires: text in the editor below.
  function requiresPanel(req) {
    const actions = new Set(req.actions || []);
    const utils = new Set(req.utils || []);
    const GATED = ["write_util", "memory_read", "memory_write"];
    const actionBoxes = GATED.map((a) => {
      const cb = el("input", { type: "checkbox", checked: actions.has(a) ? "" : null });
      cb.onchange = () => cb.checked ? actions.add(a) : actions.delete(a);
      return el("label", { class: "row", style: "gap:5px" }, cb, a);
    });
    const utilNames = [...new Set([...(data.utils || []).map((u) => u.name), ...utils])].sort();
    const utilBoxes = utilNames.map((u) => {
      const cb = el("input", { type: "checkbox", checked: utils.has(u) ? "" : null });
      cb.onchange = () => cb.checked ? utils.add(u) : utils.delete(u);
      return el("label", { class: "row", style: "gap:5px" }, cb, u);
    });
    const runsSel = el("select", {}, ...[["", "(none)"], ["last", "last run"], ["all", "all runs"]]
      .map(([v, label]) => el("option", { value: v, selected: (req.runs || "") === v ? "" : null }, label)));
    const node = el("div", { class: "panel", style: "margin-bottom:10px" },
      el("div", { class: "lbl" }, "requires — the capabilities this doc's instructions presume"),
      el("div", { class: "muted small", style: "margin:4px 0 8px" },
        "activating the permission on a routine switches these on; switching one off there ",
        "deactivates the permission. This panel is authoritative for the requires: key on save."),
      el("div", { class: "row", style: "gap:16px;flex-wrap:wrap;align-items:flex-start" },
        el("div", {}, el("div", { class: "muted small" }, "gated actions"), ...actionBoxes),
        el("div", {}, el("div", { class: "muted small" }, "reserved utils"),
          el("div", { style: "max-height:130px;overflow:auto" }, ...utilBoxes)),
        el("div", {}, el("div", { class: "muted small" }, "previous runs"), runsSel)));
    return { node, value: () => ({
      ...(actions.size ? { actions: [...actions] } : {}),
      ...(utils.size ? { utils: [...utils] } : {}),
      ...(runsSel.value ? { runs: runsSel.value } : {}) }) };
  }
  async function openUtil(name) {
    openSub = `util/${name}`; updateURL();
    const d = await api(`/api/library/utils/${name}`);
    showEditor(`util: ${name} (selftest-gated)`, d.content, null, async (content) =>
      api(`/api/library/utils/${name}`, { method: "PUT", body: { content } }), "python");
  }

  // A playbook is a subfolder (MAIN.md + optional detail files) — the editor edits MAIN.md; its
  // detail files are managed by the Update-playbook distillation, viewable read-only here.
  async function openPlaybook(slug) {
    openSub = `playbook/${slug}`; updateURL();
    const d = await api(`/api/playbooks/${slug}`);
    const extra = d.details?.length
      ? el("div", { class: "panel", style: "margin-bottom:10px" },
          el("div", { class: "lbl" }, "on-demand detail files (read-only — revised by Update playbook)"),
          d.details.map((name) => {
            const pre = el("pre", { class: "prose small",
              style: "display:none;white-space:pre-wrap;max-height:300px;overflow:auto;margin:6px 0" });
            const link = el("a", { href: "#", onclick: async (e) => {
              e.preventDefault();
              if (pre.style.display === "none") {
                if (!pre.textContent) {
                  try {
                    const f = await api(`/api/playbooks/${slug}/detail/${encodeURIComponent(name)}`);
                    pre.textContent = f.content || "(empty)";
                  } catch (err) { pre.textContent = err.message; }
                }
                pre.style.display = "block";
              } else { pre.style.display = "none"; }
            } }, name);
            return el("div", {}, link, pre);
          }))
      : null;
    showEditor(`playbook: ${slug} (MAIN.md)`, d.content, d.log, async (content) =>
      api(`/api/playbooks/${slug}`, { method: "PUT", body: { content } }), undefined,
      async () => {
        if (!confirm(`Delete playbook "${slug}"? It is git-versioned — recoverable from history.`)) return false;
        await api(`/api/playbooks/${slug}`, { method: "DELETE" });
        return true;
      }, extra);
  }

  // workflows + utils are Python → highlighted editor; traits/permissions are markdown → plain.
  // `extra` renders above the editor (the permissions requires: panel).
  function showEditor(label, content, log, save, lang, del, extra) {
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
      extra || "",
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
                     playbook: openPlaybook,
                     util: openUtil }[kind];
    if (opener && id) opener(id).catch((e) => toast(e.message, 4000, { error: true }));
  }

}
