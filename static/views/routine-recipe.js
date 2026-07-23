// The recipe editor (split from routine.js): the navigable tree of the routine's OWN
// workflow files (main.md + stage modules + traits), a per-file edit/preview pane with
// save+commit, and heading deep-links. mountRecipe fills the two columns and returns
// { refreshTree } (recipe health's roll-back re-syncs the tree through it).

import { api } from "/static/api.js";
import { el, toast } from "/static/util.js";
import { md } from "/static/md.js";
import { recipeNav } from "/static/components/recipenav.js";
import { setQuery } from "/static/router.js";

export function mountRecipe(navCol, editorCol, slug, initialFile) {
  let recipeTree = null;
  let currentFile = initialFile || "";
  function renderNav() {
    if (recipeTree) navCol.replaceChildren(recipeNav(recipeTree, openFile, currentFile));
  }
  async function refreshTree() {
    try { recipeTree = await api(`/api/routines/${slug}/recipe`); renderNav(); } catch { /* keep last */ }
  }
  async function openFile(path, heading, silent = false) {
    currentFile = path;
    renderNav();
    let data;
    try { data = await api(`/api/routines/${slug}/file?path=${encodeURIComponent(path)}`); }
    catch (err) { toast(err.message, 4000, { error: true }); return; }
    editorCol.replaceChildren(fileEditorPane(path, data.content, heading));
    if (!silent) { setQuery({ file: path }); editorCol.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
  }
  api(`/api/routines/${slug}/recipe`).then((t) => {
    recipeTree = t; renderNav();
    if (currentFile) openFile(currentFile, null, true);   // restore an open file from the URL
  }).catch((err) => navCol.replaceChildren(el("div", { class: "muted" }, `couldn't load recipe: ${err.message}`)));

  // One file's editor: an edit/preview toggle (rendered markdown via md()), save + commit through
  // /file, and — when opened via a heading in the tree — a scroll to that heading.
  function fileEditorPane(path, content, heading) {
    const ta = el("textarea", { class: "code recipe-ta" }, content || "");
    const preview = el("div", { class: "prose recipe-preview", style: "display:none" });
    const editBtn = el("button", { class: "btn small primary" }, "edit");
    const prevBtn = el("button", { class: "btn small" }, "preview");
    const setMode = (previewing) => {
      if (previewing) preview.replaceChildren(md(ta.value));
      preview.style.display = previewing ? "" : "none";
      ta.style.display = previewing ? "none" : "";
      editBtn.classList.toggle("primary", !previewing);
      prevBtn.classList.toggle("primary", previewing);
    };
    editBtn.onclick = () => setMode(false);
    prevBtn.onclick = () => setMode(true);
    const saveBtn = el("button", { class: "btn primary" }, "save");
    saveBtn.onclick = async () => {
      try {
        await api(`/api/routines/${slug}/file`, { method: "PUT", body: { path, content: ta.value } });
        toast(`${path} saved`); refreshTree();   // headings may have changed
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    if (heading) requestAnimationFrame(() => scrollToHeading(ta, heading));
    return el("div", {},
      el("div", { class: "row spread", style: "margin-bottom:8px" },
        el("span", { class: "ref-tag" }, path),
        el("div", { class: "row" }, editBtn, prevBtn)),
      ta, preview,
      el("div", { class: "row mt" }, saveBtn));
  }

  function scrollToHeading(ta, heading) {
    const lines = ta.value.split("\n");
    const needle = heading.trim();
    const idx = lines.findIndex((l) => /^#{1,4}\s/.test(l)
      && l.replace(/^#{1,4}\s+/, "").replace(/`/g, "").trim() === needle);
    if (idx < 0) return;
    const offset = lines.slice(0, idx).reduce((n, l) => n + l.length + 1, 0);
    ta.focus();
    ta.setSelectionRange(offset, offset);
    const lh = parseFloat(getComputedStyle(ta).lineHeight) || 18;
    ta.scrollTop = Math.max(0, (idx - 1) * lh);
  }
  return { refreshTree };
}
