// The run's file activity — which files were read / written / edited, per-path counts
// derived server-side from the transcript (/api/runs/…/files: observation events, so
// subruns and user slash commands count too). The transcript shows every touch in
// order; this rail card answers "what did this run touch" at a glance, rows in
// first-touched order. poke() coalesces a live burst of file observations into one
// refetch, so the live tail can call it per event without hammering the endpoint.

import { api, apiBlobUrl } from "/static/api.js";
import { newTabHref } from "/static/components/blobtab.js";
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
  // /api/runs/<id>/files → /api/runs/<id>/file?path=… — one row's bytes, same auth
  const fileBase = url.replace(/\/files$/, "/file");

  // Fetch one row's file with the auth header and hand it to the browser as a blob —
  // view in a new tab (sandboxed unless the type cannot carry script: blobtab.js) or
  // download. Rows outside the served scope (fs-root paths) get told so inline, where the
  // click happened, instead of a dead new tab.
  async function grab(path, ops, download) {
    try {
      const { url: burl, type } = await apiBlobUrl(`${fileBase}?path=${encodeURIComponent(path)}`);
      const name = path.split("/").pop();
      const tab = download ? null : newTabHref(burl, type, name);
      const a = el("a", download ? { href: burl, download: name }
                                 : { href: tab.href, target: "_blank" });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => { URL.revokeObjectURL(burl); tab?.revoke(); }, 60_000);
    } catch (err) {
      const prev = ops.textContent;
      ops.textContent = err.status === 400 ? "outside served scope" : "unavailable";
      setTimeout(() => { ops.textContent = prev; }, 4000);
    }
  }

  function paint(files, history) {
    box.replaceChildren();
    if (!files.length && !history.length) {
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
      const row = box.lastChild;
      const ops = row.querySelector(".file-ops");
      row.append(
        el("button", { class: "file-act", title: "open in a new tab",
                       onclick: () => grab(f.path, ops, false) }, "⧉"),
        el("button", { class: "file-act", title: "download",
                       onclick: () => grab(f.path, ops, true) }, "⭳"));
    }
    if (history.length) {
      // the context the model no longer carries verbatim -- compaction's archive files,
      // one per pass; servable like any files-card row (user order 2026-08-12)
      box.append(el("div", { class: "rail-sub faint small" }, "compacted history"));
      for (const name of history) {
        const hops = el("span", { class: "file-ops" }, "archived");
        box.append(el("div", { class: "file-row hist",
                              title: `history/${name} - context archived by compaction` },
          el("span", { class: "file-path" }, "↷ " + name), hops,
          el("button", { class: "file-act", title: "open in a new tab",
                         onclick: () => grab(`history/${name}`, hops, false) }, "⧉"),
          el("button", { class: "file-act", title: "download",
                         onclick: () => grab(`history/${name}`, hops, true) }, "⭳")));
      }
    }
  }

  let loadedOnce = false;
  async function refresh() {
    try {
      const d = await api(url);
      paint(d.files || [], d.history || []);
      loadedOnce = true;
    }
    catch (err) {
      // decoration on refresh (keep the last render) — but a FIRST load failing
      // must not read as "no file activity"
      if (!loadedOnce) box.replaceChildren(
        el("div", { class: "faint small" }, `file activity unavailable — ${err.message}`));
    }
  }

  refresh();
  return {
    refresh,
    poke() {  // live tail: many file observations arrive in bursts — one refetch per lull
      if (timer) return;
      // …and none once the card is gone: a view left inside the lull is torn down, and its
      // refetch would be a request for a card nobody can see
      timer = setTimeout(() => { timer = null; if (box.isConnected) refresh(); }, 1500);
    },
  };
}
