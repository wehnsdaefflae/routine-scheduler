// The run's file activity — which files were read / written / edited, per-path counts
// derived server-side from the transcript (/api/runs/…/files: observation events, so
// subruns and user slash commands count too). The transcript shows every touch in
// order; this rail card answers "what did this run touch" at a glance, rows in
// first-touched order. poke() coalesces a live burst of file observations into one
// refetch, so the SSE tail can call it per event without hammering the endpoint.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

function opsLine(f) {
  const ops = [];
  if (f.reads) ops.push(f.reads > 1 ? `read ×${f.reads}` : "read");
  if (f.writes) ops.push(f.writes > 1 ? `wrote ×${f.writes}` : "wrote");
  if (f.edits) ops.push(f.edits > 1 ? `edit ×${f.edits}` : "edit");
  if (f.errors) ops.push(`✕${f.errors}`);
  return ops.join(" · ");
}

export function createFileActivity(container, { url }) {
  const box = el("div", { class: "filelist" });
  container.append(box);
  let timer = null;

  function paint(files) {
    box.replaceChildren();
    if (!files.length) {
      box.append(el("div", { class: "faint small" }, "no files read or written"));
      return;
    }
    for (const f of files) {
      const detail = [f.path, opsLine(f),
                      f.bytes ? `${f.bytes} bytes written` : "",
                      f.sub ? "touched in a subrun" : ""].filter(Boolean).join("\n");
      // LRM sentinels: the path renders rtl so long paths truncate LEFT (the filename
      // end is the informative part); the marks keep leading/trailing slashes in place.
      box.append(el("div", { class: `file-row${f.errors ? " err" : ""}`, title: detail },
        el("span", { class: "file-path" }, "\u200e" + (f.sub ? "↳ " : "") + f.path + "\u200e"),
        el("span", { class: "file-ops" }, opsLine(f))));
    }
  }

  async function refresh() {
    try { paint((await api(url)).files || []); }
    catch { /* instrumentation is decoration — never break the view */ }
  }

  refresh();
  return {
    refresh,
    poke() {  // live tail: many file observations arrive in bursts — one refetch per lull
      if (timer) return;
      timer = setTimeout(() => { timer = null; refresh(); }, 1500);
    },
  };
}
