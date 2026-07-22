// A server-side directory picker modal: browse the DAEMON's filesystem (via /api/fs/list) and
// pick a real path, instead of typing one blind. Same overlay language as dialog.js. Click a
// folder to descend, "⤴ .." to go up, or type/paste a path and press Enter to jump; "select
// this folder" resolves the currently-shown directory. Promise-based:
//   const dir = await pickDirectory({ title: "read root" }); if (dir == null) return;

import { api } from "/static/api.js";
import { el } from "/static/util.js";

export function pickDirectory({ title = "Select a directory", start = "" } = {}) {
  return new Promise((resolve) => {
    let cur = start;
    const done = (v) => { document.removeEventListener("keydown", onKey); overlay.remove(); resolve(v); };

    const pathInput = el("input", { type: "text", class: "code", style: "flex:1;min-width:0",
      placeholder: "/path/to/directory", "data-nopersist": true });
    const listBox = el("div", { class: "dirpicker-list" });
    const note = el("div", { class: "muted small", style: "min-height:16px" }, "");

    function row(icon, name, onClick) {
      return el("div", { class: "dp-row" + (onClick ? "" : " file"),
        ...(onClick ? { onclick: onClick, title: "open" } : {}) },
        el("span", { class: "dp-ic" }, icon), el("span", { class: "dp-name" }, name));
    }

    async function load(path) {
      listBox.replaceChildren(el("div", { class: "muted small", style: "padding:8px" }, "loading…"));
      note.textContent = "";
      let data;
      try { data = await api(`/api/fs/list?path=${encodeURIComponent(path || "")}`); }
      catch (err) {
        listBox.replaceChildren(el("div", { class: "small", style: "padding:8px;color:var(--err)" }, err.message));
        return;
      }
      cur = data.path;
      pathInput.value = data.path;
      const rows = [];
      if (data.parent) rows.push(row("⤴", "..", () => load(data.parent)));
      for (const e of data.entries)
        rows.push(e.is_dir ? row("📁", e.name, () => load(e.path)) : row("📄", e.name, null));
      listBox.replaceChildren(rows.length
        ? el("div", {}, ...rows)
        : el("div", { class: "muted small", style: "padding:8px" }, "(empty directory)"));
      if (data.truncated) note.textContent = "…directory truncated (too many entries to list all)";
    }

    const ok = el("button", { class: "btn primary" }, "select this folder");
    ok.onclick = () => done(cur);
    const cancel = el("button", { class: "btn" }, "cancel");
    cancel.onclick = () => done(null);
    const go = el("button", { class: "btn small", onclick: () => load(pathInput.value.trim()) }, "go");
    pathInput.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); load(pathInput.value.trim()); } };

    const overlay = el("div", { class: "modal-overlay" },
      el("div", { class: "panel dirpicker" },
        el("div", { class: "dlg-msg", style: "font-weight:600" }, title),
        el("div", { class: "row", style: "gap:6px" }, pathInput, go),
        listBox, note,
        el("div", { class: "row", style: "justify-content:flex-end;gap:8px" }, cancel, ok)));
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); done(null); } };
    document.addEventListener("keydown", onKey);
    overlay.onclick = (e) => { if (e.target === overlay) done(null); };
    document.body.append(overlay);
    load(start);
  });
}
