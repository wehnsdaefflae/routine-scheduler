// Attachment picker shared by the new-conversation composer and the reply composer:
// file dialog + removable chips + paste-to-attach (a pasted screenshot becomes a named
// file). Returns { picker, files(), clearFiles(), wirePaste(target) }.

import { el } from "/static/util.js";

export function filePicker() {
  const input = el("input", { type: "file", multiple: true, hidden: true });
  const chips = el("span", { class: "attach-chips" });
  const btn = el("button", { class: "btn small", onclick: () => input.click() }, "📎 attach");
  let pending = [];
  const renderChips = () => {
    chips.replaceChildren(...pending.map((f, i) =>
      el("span", { class: "attach-chip removable", title: "click to remove",
        onclick: () => { pending.splice(i, 1); renderChips(); } }, f.name, " ×")));
  };
  const addFiles = (list) => {
    for (const f of list) {
      // a pasted screenshot arrives as a nameless/generic blob — give it a real name
      const name = f.name && f.name !== "image.png" ? f.name
        : `pasted-${Date.now()}.${(f.type.split("/")[1] || "png").replace("+xml", "")}`;
      pending.push(new File([f], name, { type: f.type }));
    }
    renderChips();
  };
  input.onchange = () => { addFiles([...input.files]); input.value = ""; };
  // Ctrl/Cmd-V straight into the message box: clipboard files (screenshots, copied
  // images/documents) become attachments; plain text pastes stay untouched.
  const wirePaste = (target) => target.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    addFiles(files);
  });
  return {
    picker: el("span", { class: "row", style: "gap:6px" }, btn, input, chips),
    files: () => [...pending],
    clearFiles: () => { pending = []; input.value = ""; renderChips(); },
    wirePaste,
  };
}
