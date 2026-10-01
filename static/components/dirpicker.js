// A server-side directory picker modal: browse the DAEMON's filesystem (via /api/fs/list) and
// pick a real path, instead of typing one blind. A dialog.js modal (openModal): named by its
// title, the path field focused on open, focus trapped and given back, Escape or a click on the
// scrim to cancel. Click a folder to descend, "⤴ .." to go up, or type/paste a path and press
// Enter to jump; "select this folder" resolves the currently-shown directory. Promise-based:
//   const dir = await pickDirectory({ title: "read root" }); if (dir == null) return;

import { api } from "/static/api.js";
import { openModal } from "/static/components/dialog.js";
import { el } from "/static/util.js";

export function pickDirectory({ title = "Select a directory", start = "" } = {}) {
  return new Promise((resolve) => {
    let cur = start;
    const done = (v) => { close(); resolve(v); };

    const pathInput = el("input", { type: "text", class: "code", style: "flex:1;min-width:0",
      placeholder: "/path/to/directory", "data-nopersist": true });
    const listBox = el("div", { class: "dirpicker-list" });
    const note = el("div", { class: "muted small", style: "min-height:16px" }, "");

    // A folder row is a BUTTON, so Tab reaches it and Enter/Space opens it; a file row is
    // inert text. They were all divs with click handlers — a mouse-only list inside a dialog
    // whose whole promise is the keyboard.
    function row(icon, name, onClick) {
      const parts = [el("span", { class: "dp-ic", "aria-hidden": "true" }, icon),
                     el("span", { class: "dp-name" }, name)];
      return onClick
        ? el("button", { type: "button", class: "dp-row", onclick: onClick, title: "open" }, ...parts)
        : el("div", { class: "dp-row file" }, ...parts);
    }

    // Only the NEWEST listing may paint: the opening listing still in flight when the reader
    // types a path and presses go would otherwise land second and put the old folder back.
    let latest = 0;
    async function load(path) {
      const mine = ++latest;
      listBox.replaceChildren(el("div", { class: "muted small", style: "padding:8px" }, "loading…"));
      note.textContent = "";
      let data;
      try { data = await api(`/api/fs/list?path=${encodeURIComponent(path || "")}`); }
      catch (err) {
        if (mine !== latest) return;
        listBox.replaceChildren(el("div", { class: "small", style: "padding:8px;color:var(--err)" }, err.message));
        return;
      }
      if (mine !== latest) return;
      cur = data.path;
      pathInput.value = data.path;
      const rows = [];
      if (data.parent) rows.push(row("⤴", "..", () => load(data.parent)));
      for (const e of data.entries) {
        // F190: an entry the daemon cannot stat stays VISIBLE and descendable — clicking
        // it surfaces the endpoint's explicit error instead of a silent gap in the list.
        const r = e.is_dir ? row(e.unreadable ? "🔒" : "📁", e.name, () => load(e.path))
                           : row("📄", e.name, null);
        if (e.unreadable) r.title = "not readable by the daemon — open it to see the exact error";
        rows.push(r);
      }
      if (!data.entries.length) {
        // the parent-nav row is not an entry: an empty directory says so even when ".." shows
        rows.push(el("div", { class: "muted small", style: "padding:8px" },
          "(empty directory — as the DAEMON sees it: mounts hidden from its service or "
          + "container namespace don't appear here; type a path above to jump directly)"));
      }
      listBox.replaceChildren(el("div", {}, ...rows));
      if (data.truncated) note.textContent = "…directory truncated (too many entries to list all)";
    }

    const ok = el("button", { class: "btn primary" }, "select this folder");
    ok.onclick = () => done(cur);
    const cancel = el("button", { class: "btn" }, "cancel");
    cancel.onclick = () => done(null);
    const go = el("button", { class: "btn small", onclick: () => load(pathInput.value.trim()) }, "go");
    pathInput.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); load(pathInput.value.trim()); } };

    const heading = el("div", { class: "dlg-msg", style: "font-weight:600" }, title);
    const close = openModal(
      el("div", { class: "panel dirpicker" },
        heading,
        el("div", { class: "row", style: "gap:6px" }, pathInput, go),
        listBox, note,
        el("div", { class: "row", style: "justify-content:flex-end;gap:8px" }, cancel, ok)),
      { label: heading, focus: pathInput, onCancel: () => done(null) });
    load(start);
  });
}
