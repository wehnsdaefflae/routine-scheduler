// The conversation's artifact panel: everything the model wrote into artifacts/ — listed
// newest-first and rendered inline by type. Files are fetched WITH the auth header and
// rendered from blob URLs (iframes/imgs can't carry Authorization); html renders in a
// sandboxed iframe (scripts yes, same-origin no — an artifact can never read the console's
// token). Re-writing the same filename updates the artifact in place: refresh() re-lists.

import { api, apiBlobUrl } from "/static/api.js";
import { md } from "/static/md.js";
import { el, emptyState, relTime } from "/static/util.js";

const IMG = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp"]);
const AUDIO = new Set(["mp3", "wav", "ogg", "m4a", "flac"]);
const VIDEO = new Set(["mp4", "webm", "mov"]);
const TEXTUAL = new Set(["txt", "log", "py", "js", "ts", "sh", "yaml", "yml", "toml", "ini",
                         "xml", "sql", "css", "diff", "patch", "env", "conf", "jsonl"]);
const ICON = (ext) => IMG.has(ext) ? "🖼" : AUDIO.has(ext) ? "🔊" : VIDEO.has(ext) ? "🎞"
  : ext === "pdf" ? "📕" : ext === "html" ? "🌐" : ext === "md" ? "📄"
  : ext === "csv" || ext === "tsv" ? "🧮" : ext === "json" ? "{}" : "📎";

function csvTable(text, sep) {
  const rows = text.replace(/\r/g, "").split("\n").filter((r) => r.length).slice(0, 200)
    .map((r) => r.split(sep));
  const table = el("table", { class: "art-table" });
  rows.forEach((cells, i) => {
    const tr = el("tr", {});
    for (const c of cells.slice(0, 30)) tr.append(el(i === 0 ? "th" : "td", {}, c));
    table.append(tr);
  });
  return el("div", { class: "art-scroll" }, table);
}

export function createArtifacts(container, { slug }) {
  const listBox = el("div", { class: "art-list" });
  const viewer = el("div", { class: "art-viewer", hidden: true });
  container.append(el("div", { class: "art-head" }, el("strong", {}, "Artifacts")),
                   listBox, viewer);
  let items = [];
  let openPath = null;
  let blobUrl = null;   // the viewer's current object URL (revoked on replace)

  const fileUrl = (p) => `/api/conversations/${slug}/file?path=${encodeURIComponent(p)}`;

  async function open(item) {
    openPath = item.path;
    renderList();
    viewer.hidden = false;
    viewer.replaceChildren(el("div", { class: "faint small" }, "loading…"));
    const ext = (item.name.split(".").pop() || "").toLowerCase();
    try {
      if (blobUrl) { URL.revokeObjectURL(blobUrl); blobUrl = null; }
      let body;
      if (ext === "md" || ext === "csv" || ext === "tsv" || ext === "json" || TEXTUAL.has(ext)) {
        const { url } = await apiBlobUrl(fileUrl(item.path));
        blobUrl = url;
        const text = await (await fetch(url)).text();
        body = ext === "md" ? el("div", { class: "prose" }, md(text))
          : ext === "csv" || ext === "tsv" ? csvTable(text, ext === "csv" ? "," : "\t")
          : el("pre", { class: "art-pre" }, ext === "json"
              ? JSON.stringify(JSON.parse(text), null, 2) : text);
      } else {
        const { url } = await apiBlobUrl(fileUrl(item.path));
        blobUrl = url;
        body = ext === "html"
          ? el("iframe", { class: "art-frame", sandbox: "allow-scripts", src: url })
          : IMG.has(ext) ? el("img", { class: "art-img", src: url, alt: item.name })
          : ext === "pdf" ? el("iframe", { class: "art-frame tall", src: url })
          : AUDIO.has(ext) ? el("audio", { controls: true, src: url })
          : VIDEO.has(ext) ? el("video", { class: "art-img", controls: true, src: url })
          : el("div", { class: "faint" }, "no inline view for this type — download below");
      }
      const dl = el("a", { class: "btn small", href: blobUrl, download: item.name }, "⭳ download");
      const pop = el("a", { class: "btn small", href: blobUrl, target: "_blank",
                            title: "open full-size in a new tab" }, "⧉ open");
      viewer.replaceChildren(
        el("div", { class: "art-viewer-head" },
          el("span", { class: "art-name", title: item.path }, item.name), pop, dl,
          el("button", { class: "btn small", onclick: () => { viewer.hidden = true; openPath = null; renderList(); } }, "×")),
        body);
    } catch (err) {
      viewer.replaceChildren(el("div", { class: "faint" }, `could not load: ${err.message}`));
    }
  }

  function renderList() {
    listBox.replaceChildren();
    if (!items.length) {
      listBox.append(emptyState("⬡", "No artifacts yet",
        "Deliverables the agent writes to artifacts/ appear here."));
      return;
    }
    for (const it of items) {
      const ext = (it.name.split(".").pop() || "").toLowerCase();
      listBox.append(el("button",
        { class: `art-item${openPath === it.path ? " on" : ""}`, onclick: () => open(it) },
        el("span", { class: "art-ico" }, ICON(ext)),
        el("span", { class: "art-label" },
          el("span", { class: "art-name" }, it.name),
          el("span", { class: "faint small" },
            `${relTime(new Date(it.mtime * 1000))} · ${(it.size / 1024).toFixed(it.size > 10240 ? 0 : 1)}kB`))));
    }
  }

  async function refresh() {
    try { items = await api(`/api/conversations/${slug}/artifacts`); }
    catch { return; }
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
    destroy() { if (blobUrl) URL.revokeObjectURL(blobUrl); },
  };
}
