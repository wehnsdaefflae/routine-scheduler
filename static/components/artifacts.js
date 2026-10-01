// The artifact panel (conversations AND routines): everything the model wrote into a
// deliverable dir — artifacts/, reports/ or output/ (web/artifacts.py ARTIFACT_DIRS; R339:
// scanning artifacts/ alone left the panel empty for a run that committed a real reports/
// PDF) — listed newest-first and rendered inline by type. Files are fetched WITH the
// auth header and rendered from blob URLs (iframes/imgs can't carry Authorization); html
// renders in a sandboxed iframe (scripts yes, same-origin no — an artifact can never read
// the console's token), and the "open" link hands a new tab the same sandbox (blobtab.js).
// Re-writing the same filename updates the artifact in place: refresh() re-lists. `base`
// picks the API family: "conversations" (default) | "routines".

import { api, apiBlobUrl } from "/static/api.js";
import { newTabHref } from "/static/components/blobtab.js";
import { confirmDialog } from "/static/components/dialog.js";
import { md } from "/static/md.js";
import { el, emptyState, relTime, toast } from "/static/util.js";

const IMG = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp"]);
const AUDIO = new Set(["mp3", "wav", "ogg", "m4a", "flac"]);
const VIDEO = new Set(["mp4", "webm", "mov"]);
const TEXTUAL = new Set(["txt", "log", "py", "js", "ts", "sh", "yaml", "yml", "toml", "ini",
                         "xml", "sql", "css", "diff", "patch", "env", "conf", "jsonl"]);
const ICON = (ext) => IMG.has(ext) ? "🖼" : AUDIO.has(ext) ? "🔊" : VIDEO.has(ext) ? "🎞"
  : ext === "pdf" ? "📕" : ext === "html" ? "🌐" : ext === "md" ? "📄"
  : ext === "csv" || ext === "tsv" ? "🧮" : ext === "json" ? "{}" : "📎";

// The deliverable dirs (web/artifacts.py ARTIFACT_DIRS): a write that lands in one is a row of
// this panel, which is how a conversation knows to refresh it while a reply is still working.
const DELIVERABLE = /(?:^|\/)(?:artifacts|reports|output)\//;
export const isDeliverable = (path) => DELIVERABLE.test(String(path || ""));

const MAX_ROWS = 200, MAX_COLS = 30;

// Delimited rows the way every csv writer produces them (RFC 4180): a QUOTED field may hold the
// separator, a doubled quote and a line break. Splitting each line on the separator tore a value
// like "Smith, John" into two columns and shifted the rest of its row under the wrong headers.
// Blank lines are skipped; parsing stops once the table has all the rows it will show.
function delimitedRows(text, sep) {
  const rows = [];
  let row = [], field = "", quoted = false;
  const endRow = () => {
    row.push(field);
    if (row.length > 1 || row[0] !== "") rows.push(row);
    row = [];
    field = "";
  };
  for (let i = 0; i < text.length && rows.length < MAX_ROWS; i++) {
    const c = text[i];
    if (quoted) {
      if (c !== '"') field += c;
      else if (text[i + 1] === '"') { field += '"'; i++; }
      else quoted = false;
    } else if (c === '"' && field === "") quoted = true;
    else if (c === sep) { row.push(field); field = ""; }
    else if (c === "\n") endRow();
    else if (c !== "\r") field += c;
  }
  if (rows.length < MAX_ROWS && (field || row.length)) endRow();
  return rows;
}

function csvTable(text, sep) {
  const table = el("table", { class: "art-table" });
  delimitedRows(text, sep).forEach((cells, i) => {
    const tr = el("tr", {});
    for (const c of cells.slice(0, MAX_COLS)) tr.append(el(i === 0 ? "th" : "td", {}, c));
    table.append(tr);
  });
  return el("div", { class: "art-scroll" }, table);
}

// A .json artifact is shown indented — and as the text it is when it does not parse, because a
// model-written file one trailing comma off is still worth reading ("could not load" was not).
function jsonText(text) {
  try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text; }
}

export function createArtifacts(container, { slug, base = "conversations" }) {
  const listBox = el("div", { class: "art-list" });
  const viewer = el("div", { class: "art-viewer", hidden: true });
  container.append(listBox, viewer);   // the pane's cap already says "artifacts"
  let items = [];
  let openPath = null;
  let blobUrl = null;   // the viewer's current object URL (revoked on replace)
  let tabLink = null;   // the new-tab href built from it (blobtab.js), revoked with it
  const release = () => {
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    tabLink?.revoke();
    blobUrl = null;
    tabLink = null;
  };

  const fileUrl = (p) => (base === "routines"
    ? `/api/routines/${slug}/artifact?path=${encodeURIComponent(p)}`
    : `/api/conversations/${slug}/file?path=${encodeURIComponent(p)}`);

  // Only the NEWEST open may paint the viewer or keep its object URL. Two clicks in quick
  // succession put two fetches in flight; the earlier one landing last used to repaint the
  // viewer with the file the reader had already moved off — under the other row's highlight —
  // and its URL was never revoked. Closing the viewer supersedes a fetch the same way.
  let opening = 0;

  function close() {
    opening += 1;
    release();
    viewer.hidden = true;
    viewer.replaceChildren();   // a playing <audio>/<video> stops with its viewer
    openPath = null;
  }

  async function open(item) {
    const mine = ++opening;
    openPath = item.path;
    renderList();
    viewer.hidden = false;
    viewer.replaceChildren(el("div", { class: "faint small" }, "loading…"));
    const ext = (item.name.split(".").pop() || "").toLowerCase();
    release();
    try {
      const { url, type } = await apiBlobUrl(fileUrl(item.path));
      if (mine !== opening) { URL.revokeObjectURL(url); return; }
      blobUrl = url;
      tabLink = newTabHref(url, type, item.name);
      let body;
      if (ext === "md" || ext === "csv" || ext === "tsv" || ext === "json" || TEXTUAL.has(ext)) {
        const text = await (await fetch(url)).text();
        if (mine !== opening) return;      // the newer open already released this URL
        body = ext === "md" ? el("div", { class: "prose" }, md(text))
          : ext === "csv" || ext === "tsv" ? csvTable(text, ext === "csv" ? "," : "\t")
          : el("pre", { class: "art-pre" }, ext === "json" ? jsonText(text) : text);
      } else {
        body = ext === "html"
          ? el("iframe", { class: "art-frame", sandbox: "allow-scripts", src: url })
          : IMG.has(ext) ? el("img", { class: "art-img", src: url, alt: item.name })
          : ext === "pdf" ? el("iframe", { class: "art-frame tall", src: url })
          : AUDIO.has(ext) ? el("audio", { controls: true, src: url })
          : VIDEO.has(ext) ? el("video", { class: "art-img", controls: true, src: url })
          : el("div", { class: "faint" }, "no inline view for this type — download below");
      }
      const dl = el("a", { class: "btn small", href: blobUrl, download: item.name }, "⭳ download");
      const pop = el("a", { class: "btn small", href: tabLink.href, target: "_blank",
                            title: "open full-size in a new tab" }, "⧉ open");
      viewer.replaceChildren(
        el("div", { class: "art-viewer-head" },
          el("span", { class: "art-name", title: item.path }, item.name), pop, dl,
          el("button", { class: "btn small", title: "close the viewer",
                         onclick: () => { close(); renderList(); } }, "×")),
        body);
    } catch (err) {
      if (mine !== opening) return;
      viewer.replaceChildren(el("div", { class: "faint" }, `could not load: ${err.message}`));
    }
  }

  function renderList() {
    listBox.replaceChildren();
    if (!items.length) {
      listBox.append(emptyState("⬡", "No artifacts yet",
        "Deliverables a run writes to artifacts/, reports/ or output/ appear here."));
      return;
    }
    for (const it of items) {
      const ext = (it.name.split(".").pop() || "").toLowerCase();
      const size = `${(it.size / 1024).toFixed(it.size > 10240 ? 0 : 1)}kB`;
      // one line per artifact — the viewer below is the star, the list just navigates.
      // The update time is VISIBLE (user order 2026-08-14): an artifact is re-written in
      // place across turns, and "which version is this" must not hide in a tooltip.
      // Two SIBLING buttons in a row. The delete used to be a span INSIDE the open button —
      // interactive content nested in interactive content, which a keyboard never reached
      // and a screen reader announced as part of the open control's name.
      const del = el("button", {
        type: "button", class: "art-del", title: `delete ${it.name}`,
        "aria-label": `delete ${it.name}`,
        onclick: async () => {
          // dialog.js is the console's replacement for every native confirm()/prompt(), and
          // toast for every alert(): a native alert blocks the main thread and its text never
          // reaches trace.js's error telemetry, so a failed delete was invisible to the
          // improve-ui lens that exists to notice exactly this.
          if (!(await confirmDialog(`Delete artifact ${it.name}? The file is removed for good.`,
                                    { confirmLabel: "delete" }))) return;
          try {
            await api(`/api/${base}/${slug}/artifacts?path=${encodeURIComponent(it.path)}`,
                      { method: "DELETE" });
            if (openPath === it.path) close();
            await refresh();
          } catch (err) {
            toast(`could not delete: ${err.message}`, 4000, { error: true });
          }
        } }, "🗑");
      listBox.append(el("div", { class: `art-row${openPath === it.path ? " on" : ""}` },
        el("button", { type: "button", class: "art-item", onclick: () => open(it),
                       title: `${it.name} · ${size}` },
          el("span", { class: "art-ico" }, ICON(ext)),
          el("span", { class: "art-name" }, it.name),
          el("span", { class: "art-time faint small" }, relTime(new Date(it.mtime * 1000))),
          el("span", { class: "faint small", style: "flex:none" }, size)),
        del));
    }
  }

  let loadedOnce = false;
  async function refresh() {
    try { items = await api(`/api/${base}/${slug}/artifacts`); loadedOnce = true; }
    catch (err) {
      // first load failing must not read as "no artifacts" — say so; later
      // transient errors keep the last good render
      if (!loadedOnce) listBox.replaceChildren(
        el("div", { class: "faint small" }, `artifacts unavailable — ${err.message}`));
      return;
    }
    renderList();
    // the open artifact may have been re-written — reload it in place
    if (openPath) {
      const cur = items.find((i) => i.path === openPath);
      if (cur) open(cur);
    }
  }

  refresh();
  return {
    refresh,
    count: () => items.length,
    /** The view's teardown: free the viewer's URLs, and those of an open still in flight. */
    destroy() { opening += 1; release(); },
  };
}
