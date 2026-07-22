// Editor for a routine's filesystem roots: a removable list of real server paths plus an
// "add directory…" button that opens the server-side picker (dirpicker.js). Replaces the old
// free-text "one path per line" textarea — you browse to a directory instead of typing it.
//   const rd = rootsEditor(d.fs_read_roots, { pickTitle: "read root" });
//   … rd.node …            // mount it
//   rd.value()             // -> ["/abs/path", …] at save time

import { pickDirectory } from "/static/components/dirpicker.js";
import { el, toast } from "/static/util.js";

export function rootsEditor(initial, { pickTitle = "Select a directory" } = {}) {
  let paths = [...(initial || [])];
  const list = el("div", { class: "roots-list" });

  function render() {
    list.replaceChildren();
    if (!paths.length) {
      list.append(el("div", { class: "muted small", style: "padding:2px 0" },
        "none — the run only sees its own directory"));
      return;
    }
    for (const p of paths) {
      list.append(el("div", { class: "root-row" },
        el("span", { class: "root-path", title: p }, p),
        el("button", { class: "btn ghost small", title: "remove this root",
          onclick: () => { paths = paths.filter((x) => x !== p); render(); } }, "×")));
    }
  }
  render();

  const addBtn = el("button", { class: "btn small", onclick: async () => {
    const picked = await pickDirectory({ title: pickTitle, start: paths[paths.length - 1] || "" });
    if (picked == null) return;
    if (paths.includes(picked)) { toast("already a root"); return; }
    paths.push(picked);
    render();
  } }, "+ add directory…");

  return {
    node: el("div", {}, list, el("div", { class: "row mt" }, addBtn)),
    value: () => [...paths],
  };
}
