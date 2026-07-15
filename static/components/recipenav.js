// The routine's recipe as a navigable tree that mirrors the markdown files: main.md (the entry),
// the stage modules in Run-flow order, and the trait modules — each expandable to its heading
// outline. onOpen(path, heading?) opens the file in the editor (scrolling to the heading if given).
// activePath highlights the file currently open in the editor.

import { el } from "/static/util.js";

export function recipeNav(tree, onOpen, activePath = "") {
  const root = el("div", { class: "recipe-nav" });

  const fileNode = (entry) => {
    const headings = entry.outline || [];
    const active = entry.path === activePath;
    const fileBtn = el("button",
      { class: "rn-file" + (active ? " active" : ""), onclick: () => onOpen(entry.path) },
      el("span", { class: "rn-name" }, entry.name || entry.path),
      headings.length ? el("span", { class: "rn-count" }, String(headings.length)) : null);
    const kids = headings.map((h) => el("button",
      { class: `rn-h rn-h${h.level}`, onclick: () => onOpen(entry.path, h.text) }, h.text));
    return el("div", { class: "rn-node" }, fileBtn,
      kids.length ? el("div", { class: "rn-headings" }, kids) : null);
  };

  const group = (label, entries) => el("div", { class: "rn-group" },
    el("div", { class: "rn-grouphead" }, label),
    entries.map(fileNode));

  root.append(group("entry", [tree.main]));
  if (tree.stages?.length) root.append(group(`stages · ${tree.stages.length}`, tree.stages));
  if (tree.traits?.length) root.append(group(`practices · ${tree.traits.length}`, tree.traits));
  return root;
}
