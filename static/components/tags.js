// The ONE tag editor — routines and conversations share it: chips with ✕ remove plus an
// inline add field, EVERY change saved immediately through onChange(nextTags) (no
// separate save button to forget). onChange must return the API promise; on failure the
// local state stays untouched so the UI never lies about what was persisted.

import { el, tagChip, toast } from "/static/util.js";

export function tagsEditor(initial, onChange, { placeholder = "add tag…" } = {}) {
  let tags = [...(initial || [])];
  const input = el("input", { type: "text", placeholder, "data-nopersist": true,
    style: "width:130px" });
  const wrap = el("span", { class: "tags" });

  const commit = async (next) => {
    try {
      await onChange(next);
      tags = next;
      draw();
      return true;
    } catch (err) {
      toast(err.message, 3000, { error: true });
      return false;
    }
  };

  function draw() {
    wrap.replaceChildren(
      ...tags.map((t) => tagChip(t, { onRemove: () => commit(tags.filter((x) => x !== t)) })),
      input);
  }

  input.onkeydown = async (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const v = input.value.trim().toLowerCase().replace(/\s+/g, "-");
    if (!v || tags.includes(v)) { input.value = ""; return; }
    if (await commit([...tags, v])) { input.value = ""; input.focus(); }
  };

  draw();
  return wrap;
}
