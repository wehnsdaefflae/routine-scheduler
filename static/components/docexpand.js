// Lazy full-doc expander for the permissions & practice-module panels (F178): each row
// gets a "▸ full description" toggle that fetches the LIBRARY doc once (GET
// /api/library/{permissions|traits}/{slug}) and renders its complete markdown under the
// row — the exact prose the run's prompt receives, so the page explains a permission or
// practice module with the text that actually governs the model's conduct, examples
// included. Frontmatter (`requires:` metadata) is stripped: panel mechanics, not prose.

import { el } from "/static/util.js";
import { api } from "/static/api.js";
import { md } from "/static/md.js";

const stripFrontmatter = (text) =>
  text.startsWith("---\n") ? text.replace(/^---\n[\s\S]*?\n---\n?/, "") : text;

// kind: "permissions" | "traits" · slug: the library doc.
// Returns {btn, body}: the caller places btn inside the row (it swallows the click so a
// wrapping <label> never flips its checkbox) and body directly under it.
export function docExpander(kind, slug) {
  const body = el("div", { class: "doc-expand-body", hidden: "" });
  let loaded = false;
  const btn = el("button", { class: "doc-expand-btn", type: "button" }, "▸ full description");
  btn.onclick = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const open = body.hidden;
    body.hidden = !open;
    btn.textContent = open ? "▾ full description" : "▸ full description";
    if (open && !loaded) {
      loaded = true;
      body.textContent = "loading…";
      try {
        const d = await api(`/api/library/${kind}/${slug}`);
        body.replaceChildren(md(stripFrontmatter(d.content || "")));
      } catch (err) {
        body.textContent = `could not load ${kind}/${slug}: ${err?.message || err}`;
      }
    }
  };
  return { btn, body };
}
