// Library: workflows (control-flow patterns), rules (general principle prose), permissions (grants), global utils.
// A tag filter narrows all three sections; deep-link #/library/workflow/<slug> opens an
// editor directly. Save failures (lint / selftest) render inline under the editor;
// decisions update in place — no page reloads.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { impactPanel } from "/static/components/impact.js";
import { codeEditor } from "/static/components/code.js";
import { replaceHash, remount } from "/static/router.js";
import { el, emptyState, requiresSummary, skeleton, tagChip, toast, when } from "/static/util.js";

// The confirm-then-DELETE protocol showEditor's delete button runs on, written once: false when
// the reader backs out (the button re-enables, nothing else happens), true once the file is gone
// (the deep link is dropped and the list remounts). Everything that varies between the four
// kinds is the WARNING — what that deletion costs and what, if anything, brings the file back —
// so the message is the argument and each caller keeps its own sentence.
const deleter = (path, message) => async () => {
  if (!(await confirmDialog(message, { confirmLabel: "delete" }))) return false;
  await api(path, { method: "DELETE" });
  return true;
};

export async function render(view, sub, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
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
    `workflows ${data.workflows.length} · rules ${data.rules.length} · permissions ${data.permissions.length} · templates ${(data.templates || []).length} · playbooks ${data.playbooks.length} · reminders ${(data.reminders || []).length} · utils ${data.utils.length}`;

  // Both the tag filter and the open editor are kept in the URL (#/library/<kind>/<slug>?tags=…)
  // so the view is shareable and restores on reload — without tearing itself down on each change.
  let openSub = sub || null;
  const active = new Set((query.tags || "").split(",").filter(Boolean));
  const updateURL = () => replaceHash(openSub ? `#/library/${openSub}` : "#/library",
    { tags: [...active].join(",") });
  const matches = (tags) => !active.size || (tags || []).some((t) => active.has(t));

  // One autosuggest input instead of the former wall of every tag as a chip (user order
  // 2026-08-13 — the library outgrew it). Active tags stay visible as removable chips;
  // the datalist offers only the not-yet-active rest.
  function renderFilterBar() {
    const all = [...new Set([...data.workflows, ...data.rules, ...data.permissions,
      ...data.playbooks, ...data.utils]
      .flatMap((x) => x.tags || []))].sort((a, b) => a.localeCompare(b));
    filterBar.replaceChildren();
    if (!all.length) return;
    const rerender = () => { updateURL(); renderFilterBar(); renderSections(); };
    filterBar.append(el("span", { class: "lbl" }, "filter"));
    for (const t of [...active]) filterBar.append(tagChip(t, {
      active: true, onClick: () => { active.delete(t); rerender(); } }));
    const input = el("input", { type: "search", list: "lib-tag-suggest",
      placeholder: active.size ? "add tag…" : "filter by tag…",
      style: "width:170px", "data-tag-filter": "", "data-nopersist": true });
    const commit = () => {
      const v = input.value.trim().toLowerCase();
      const hit = all.find((t) => t.toLowerCase() === v);
      if (!hit) return;                       // only real tags filter — free text is a typo
      active.add(hit); input.value = ""; rerender();
    };
    input.onchange = commit;                  // a datalist pick fires change
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } };
    filterBar.append(input,
      el("datalist", { id: "lib-tag-suggest" },
        ...all.filter((t) => !active.has(t)).map((t) => el("option", { value: t }))));
    if (active.size) filterBar.append(el("button", { class: "btn ghost small",
      onclick: () => { active.clear(); rerender(); } }, "clear"));
  }

  function renderSections() {
    sections.replaceChildren();
    section("Workflows", "the control-flow patterns routines follow",
      data.workflows.filter((w) => matches(w.tags)).map((w) =>
        item(w.name || w.slug, w.problems, w.tags, () => openWorkflow(w.slug), w.description,
             `#/library/workflow/${w.slug}`)));
    section("Rules", "general rules — shared practices a routine holds by reference and reads with read_rule; a revision here reaches every holder",
      data.rules.filter((f) => matches(f.tags)).map((f) => {
        // A rule carrying an ASSIST behaves differently for every holder — it can hold an
        // action or defer a finish — so the row has to say so. A rules list that omits it
        // describes a rule that no longer exists.
        const assists = f.assists || [];
        const summary = assists.length
          ? el("span", {}, f.summary || "", ...assists.map((a) =>
              el("span", { class: "chip", style: "margin-left:6px", title: a.line },
                 `${a.moment} · ${a.payload}`)))
          : f.summary;
        return item(f.slug, f.problems, f.tags, () => openDoc("rules", f.slug), summary,
                    `#/library/rule/${f.slug}`);
      }),
      el("button", { class: "btn ghost small", onclick: () => newDoc("rules") }, "+ new rule"));
    section("Permissions", "conduct docs — held per routine via its Permissions panel; the requires: frontmatter names the capabilities each doc's instructions presume (activating the doc switches them on; open a doc to edit the mapping)",
      data.permissions.filter((f) => matches(f.tags)).map((f) => {
        const req = requiresSummary(f.requires);
        const summary = req
          ? el("span", {}, f.summary || "",
              el("span", { style: "color:var(--warn)" }, ` ▸ ${req}`))
          : f.summary;
        return item(f.slug, f.problems, f.tags, () => openDoc("permissions", f.slug), summary,
                    `#/library/permission/${f.slug}`);
      }),
      el("button", { class: "btn ghost small", onclick: () => newDoc("permissions") }, "+ new permission"));
    section("Playbooks", "one-shot recipes — saved from a conversation (Save as playbook) and reused to seed a new one; MAIN.md is the always-loaded brief",
      data.playbooks.filter((p) => matches(p.tags)).map((p) =>
        item(p.title || p.slug, p.problems, p.tags, () => openPlaybook(p.slug), p.summary,
             `#/library/playbook/${p.slug}`)));
    section("Settings templates", "the named starting points a routine adopts — permissions, rules, capabilities and budgets COPIED IN once at creation (a preselection, never a layer: every value is then edited where it lives)",
      (data.templates || []).filter((t) => matches(t.tags)).map((t) =>
        item(t.slug, t.problems || [], t.tags, () => openDoc("templates", t.slug),
             t.summary, `#/library/template/${t.slug}`)),
      el("button", { class: "btn ghost small", onclick: () => newDoc("templates") },
         "+ new template"));
    // The curated half of the consequence-reminder layer. The ONE lever that has to exist
    // here is removal: an approval decides what gets IN, and without this nothing could take
    // an entry out again short of editing the library repo by hand.
    section("Consequence reminders",
      "curated (regex → consequence) cautions — a matching action is HELD before it runs, in every routine whose reminders capability is at `global`. Written by a run under approval; born local, global is earned",
      (data.reminders || []).map((r) =>
        el("tr", {},
          el("td", {}, el("code", {}, r.id)),
          el("td", {}, el("code", {}, r.regex)),
          el("td", { class: "muted prose", style: "max-width:420px" }, r.description || ""),
          el("td", {}, el("button", { class: "btn ghost small",
            onclick: () => removeReminder(r) }, "remove")))));
    // The one section with no "+ new": a util is AUTHORED by a run (`write_util`, selftest-gated,
    // at that routine's approval level), never typed in here. The description says so, because a
    // catalogue that offers every other kind a way to add one leaves the silence to be read as a
    // missing button — and a routine whose setup surface reports a util the library lacks is
    // waiting on a RUN, not on this page. Revise and delete are the levers that do live here.
    section("Global utils",
      "the tools routines run — each one written by a RUN through write_util (selftest-gated, "
      + "at that routine's approval level), which is why there is no + new here; open one to "
      + "revise or delete it",
      data.utils.filter((u) => matches(u.tags)).map((u) =>
        item(u.name, [], u.tags, () => openUtil(u.name), u.summary,
             `#/library/util/${u.name}`)));
  }

  function section(title, desc, rows, action) {
    sections.append(el("h2", {}, title));
    sections.append(el("div", { class: "panel", style: "padding:0" },
      el("div", { class: "muted small",
        style: "padding:11px 16px;border-bottom:1px solid var(--rule);display:flex;justify-content:space-between;align-items:center;gap:12px" },
        el("span", {}, desc), action || ""),
      el("div", { class: "tablewrap" },
        el("table", { class: "list" }, el("tbody", {}, rows.length ? rows
          : el("tr", {}, el("td", { class: "muted" }, active.size ? "none match this filter" : "none")))))));
  }

  function item(label, problems, tags, onopen, summary, href) {
    // a REAL href (the section deep-link) so middle-click/new-tab work; a plain click
    // still opens the inline editor without a re-route
    return el("tr", {},
      el("td", {}, el("a", { href: href || "#", onclick: (e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
        e.preventDefault(); onopen();
      } }, label)),
      el("td", {}, (tags || []).length ? el("div", { class: "tags" }, tags.map((t) => tagChip(t))) : ""),
      el("td", { class: "muted prose", style: "max-width:460px" }, summary || ""),
      el("td", {}, (problems && problems.length)
        ? el("span", { class: "chip failed", title: problems.join("\n") }, `${problems.length} lint`) : ""));
  }

  // The same confirm-then-DELETE protocol the doc editors use. Its own warning, because what
  // this deletion costs is particular: the reminder stops holding actions everywhere at the
  // next run, and each routine's evidence about it is deliberately NOT removed with it.
  async function removeReminder(r) {
    const gone = await deleter(`/library/reminders/${r.id}`,
      `Remove the curated reminder ${r.id}?\n\n/${r.regex}/ — ${r.description}\n\n`
      + "Routines whose reminders capability is at `global` stop being held by it from their "
      + "next run. Their own tallies (how often it fired, and how those fires turned out) are "
      + "each routine's own state and are left alone. Recoverable from the library's git "
      + "history.")();
    if (gone) { toast(`removed ${r.id}`); remount(); }
  }

  async function openWorkflow(slug) {
    openSub = `workflow/${slug}`; updateURL();
    const d = await api(`/api/workflows/${slug}`);
    // converse is undeletable (every conversation is materialized from it by slug) — no button
    const wfDelete = slug === "converse" ? undefined
      : deleter(`/api/workflows/${slug}`,
                `Delete workflow "${slug}"? Routines born from it keep their own `
                + "recipes. A seed pattern returns at the next daemon boot.");
    // A workflow PATTERN has no holders: a routine is born from one and keeps its own recipe,
    // so editing it reaches nobody retroactively and there is no blast radius to preview.
    showEditor(`workflow: ${slug}`, d.content, d.log, async (content) =>
      api(`/api/workflows/${slug}`, { method: "PUT", body: { content } }),
      { lang: "python", del: wfDelete });
  }
  async function openDoc(kind, slug) {
    openSub = `${kind.slice(0, -1)}/${slug}`; updateURL();
    const d = await api(`/api/library/${kind}/${slug}`);
    // permissions get a structured, prefilled requires: panel — it is authoritative for
    // that key on save (the server merges it into the frontmatter); prose stays in the editor
    const requires = kind === "permissions" ? requiresPanel(d.requires || {}) : null;
    // rules are deletable (a seed rule returns at the next boot) — permission docs are NOT
    // (the capability layer's conduct surface). There is only ONE copy of a rule, so a
    // deletion reaches every routine that holds it at its next run.
    const docDelete = kind === "rules"
      ? deleter(`/api/library/rules/${slug}`,
                `Delete rule "${slug}"? Every routine holding it loses it at `
                + "the next run; a seed rule returns at the next daemon boot.")
      : undefined;
    showEditor(`${kind.slice(0, -1)}: ${slug}`, d.content, d.log, async (content, digest) =>
      api(`/api/library/${kind}/${slug}`, { method: "PUT",
        body: { content, ...(requires ? { requires: requires.value() } : {}),
                ...(digest ? { impact_digest: digest } : {}) } }),
      { del: docDelete, extra: requires?.node, impact: impactPanel(kind, slug) });
  }

  // Author a fresh rule/permission doc: a lint-satisfying template plus a slug field; save
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
      : "---\ntags: [conduct, principle, draft]\n---\n"
        + "# rule: <name> — <one-line summary of the principle>\n\n"
        + "The principle: when it applies, what it looks like in action, what to avoid.\n"
        + "Write the GENERAL form — every routine holding it reads this same text and applies\n"
        + "it to its own case. Name no tool and no routine.\n";
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
      // a doc that does not exist yet is held by nobody — no impact panel until it is saved
    }, { extra: requires ? el("div", {}, head, requires.node) : head });
  }

  // The capabilities a permission doc's instructions presume. Prefilled from the doc's
  // frontmatter; edits here win over hand-edited requires: text in the editor below.
  function requiresPanel(req) {
    const actions = new Set(req.actions || []);
    const utils = new Set(req.utils || []);
    const GATED = ["write_util", "memory_read", "memory_write"];
    // One labelled checkbox per name, ticking membership in `set` and writing straight back to
    // it — value() below reads the two sets, never the DOM, so the boxes and the saved
    // frontmatter cannot drift apart.
    const boxes = (list, set) => list.map((name) => {
      const cb = el("input", { type: "checkbox", checked: set.has(name) ? "" : null });
      cb.onchange = () => cb.checked ? set.add(name) : set.delete(name);
      return el("label", { class: "row", style: "gap:5px" }, cb, name);
    });
    const utilNames = [...new Set([...(data.utils || []).map((u) => u.name), ...utils])].sort();
    const runsSel = el("select", {}, ...[["", "(none)"], ["last", "last run"], ["all", "all runs"]]
      .map(([v, label]) => el("option", { value: v, selected: (req.runs || "") === v ? "" : null }, label)));
    const node = el("div", { class: "panel", style: "margin-bottom:10px" },
      el("div", { class: "lbl" }, "requires — the capabilities this doc's instructions presume"),
      el("div", { class: "muted small", style: "margin:4px 0 8px" },
        "activating the permission on a routine switches these on; switching one off there ",
        "deactivates the permission. This panel is authoritative for the requires: key on save."),
      el("div", { class: "row", style: "gap:16px;flex-wrap:wrap;align-items:flex-start" },
        el("div", {}, el("div", { class: "muted small" }, "gated actions"), ...boxes(GATED, actions)),
        el("div", {}, el("div", { class: "muted small" }, "reserved utils"),
          el("div", { style: "max-height:130px;overflow:auto" }, ...boxes(utilNames, utils))),
        el("div", {}, el("div", { class: "muted small" }, "previous runs"), runsSel)));
    return { node, value: () => ({
      ...(actions.size ? { actions: [...actions] } : {}),
      ...(utils.size ? { utils: [...utils] } : {}),
      ...(runsSel.value ? { runs: runsSel.value } : {}) }) };
  }
  async function openUtil(name) {
    openSub = `util/${name}`; updateURL();
    const d = await api(`/api/library/utils/${name}`);
    showEditor(`util: ${name} (selftest-gated)`, d.content, null, async (content, digest) =>
      api(`/api/library/utils/${name}`, { method: "PUT",
        body: { content, ...(digest ? { impact_digest: digest } : {}) } }),
      { lang: "python",
        del: deleter(`/api/library/utils/${name}`,
                     `Delete util "${name}"? Every routine loses it at its next run. `
                     + "It is git-versioned — recoverable from history."),
        impact: impactPanel("utils", name) });
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
    // A playbook is picked up per conversation, never HELD in a routine's config — nothing to
    // preview a blast radius over.
    showEditor(`playbook: ${slug} (MAIN.md)`, d.content, d.log, async (content) =>
      api(`/api/playbooks/${slug}`, { method: "PUT", body: { content } }),
      { del: deleter(`/api/playbooks/${slug}`,
                     `Delete playbook "${slug}"? It is git-versioned — recoverable from history.`),
        extra });
  }

  // workflows + utils are Python → highlighted editor; rules/permissions are markdown → plain.
  // `extra` renders above the editor (the permissions requires: panel).
  // `impact` is the blast-radius panel (components/impact.js) for the kinds one copy of which
  // reaches every holder: it states WHO holds the doc on open; it gates both save and delete
  // on what a change would cost them. Kinds nobody HOLDS (workflows, playbooks) pass none.
  function showEditor(label, content, log, save, { lang, del, extra, impact } = {}) {
    editor.replaceChildren();
    const ed = codeEditor(content, { lang, minHeight: 360 });
    const errBox = el("div", {});
    const delBtn = !del ? null : el("button", { class: "btn danger small" }, "delete");
    if (delBtn) {
      delBtn.onclick = async () => {
        delBtn.disabled = true;
        try {
          // A deletion is the widest change of all — the holders lose the document with
          // nothing to fall back on — and it was the one path with no impact check at all.
          if (impact && (await impact.gate(null, { verb: "delete" })) === null) {
            delBtn.disabled = false;
            return;
          }
          if (await del()) {
            toast("deleted + committed");
            openSub = null;     // the deep link points at a file that no longer exists
            updateURL();
            remount();          // re-render the view in place — the list drops the file
            return;
          }
        } catch (err) { toast(err.message, 5000, { error: true }); }
        delBtn.disabled = false;
      };
    }
    const btn = el("button", { class: "btn primary" }, "save + commit");
    btn.onclick = async () => {
      btn.disabled = true;
      errBox.replaceChildren();
      try {
        // Preview BEFORE the write, always: a breaking save is confirmed with the digest the
        // preview returned, so the server's 409 (which no UI could answer) never fires.
        const digest = impact ? await impact.gate(ed.value, { verb: "save" }) : "";
        if (digest === null) { btn.disabled = false; return; }
        await save(ed.value, digest);
        toast("saved + committed");
        remount();   // refresh the list/tags in place; the deep link reopens this editor
        return;
      }
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
      impact ? el("div", { class: "panel" }, impact.node) : "",
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
                     rule: (id) => openDoc("rules", id),
                     permission: (id) => openDoc("permissions", id),
                     playbook: openPlaybook,
                     util: openUtil }[kind];
    if (opener && id) opener(id).catch((e) => toast(e.message, 4000, { error: true }));
  }

}
