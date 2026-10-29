// A file ATTACHMENT's row, and the object URLs its thumbnails hold open — the console's one
// place for "a file came with this message, show it".
//
// Its own module for the reason obs-background.js and obs-accounting.js are theirs:
// `static/components/transcript.js` is over the repo's 350-line house bar and frozen at its
// ratchet ceiling, so an addition belongs in a new file (CLAUDE.md Standards,
// tests/test_file_size.py). And this block was never really the transcript's: it is shared by
// the transcript's injection renderer, the conversation chat's user bubbles and a
// `view_image` observation's previews, so three callers reached THROUGH transcript.js for it
// (chat.js imported `attachmentRow` and `revokeBlobs` from there). The responsibility is the
// authenticated blob route and the LIFETIME of what it hands back, which is nothing to do with
// rendering a transcript.
//
// The lifetime rule is the whole reason `revokeBlobs` ships beside the row that creates the
// URLs: a decoded image stays in memory until its URL is revoked, so the component that
// mounted the row owns an array and revokes it in its destroy(), exactly once.

import { apiBlobUrl } from "/static/api.js";
import { newTabHref } from "/static/components/blobtab.js";
import { el, toast } from "/static/util.js";

// Inline rendering for a user message's file attachments (user_injection
// payload.attachments): images become thumbnails loaded through the authenticated
// blob route (a bare <img src> cannot carry the Authorization header — the artifact
// panel's pattern), everything else a fetch-and-open chip. Shared by the transcript's
// injection renderer and the conversation chat's user bubbles.
//
// `blobs` is the OWNER's array of object URLs (api.js: "the caller owns the URL's lifetime",
// the shape artifacts.js already holds). Every thumbnail holds its decoded image in memory
// until its URL is revoked, and a long conversation re-renders its whole history — so the
// component that mounted the row revokes them in its destroy(), exactly once.
const ATT_IMG = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"]);

// A blob URL carries THIS console's origin, so an attachment opened from one as a page of its
// own would run as the console (an SVG's script read the operator token). New tabs therefore go
// through components/blobtab.js, the console's ONE rule for it: a passive type opens as itself,
// anything that can carry script inside a sandboxed frame. A thumbnail still SHOWS an SVG — an
// <img> runs no script.
function openInTab({ url, type }, name, blobs) {
  const tab = newTabHref(url, type, name);
  if (tab.href !== url) blobs?.push(tab.href);
  window.open(tab.href, "_blank");
}

export function attachmentRow(rels, fileUrl, blobs) {
  if (!rels?.length || !fileUrl) return null;
  const row = el("div", { class: "att-row" });
  for (const rel of rels) {
    const name = String(rel).split("/").pop();
    const ext = (name.split(".").pop() || "").toLowerCase();
    if (ATT_IMG.has(ext)) {
      const img = el("img", { class: "att-thumb", alt: name, title: `${name} — click to open` });
      let file = null;   // {url, type} once the thumbnail has loaded
      img.onclick = () => { if (file) openInTab(file, name, blobs); };
      apiBlobUrl(fileUrl(rel)).then((f) => { blobs?.push(f.url); file = f; img.src = f.url; })
        .catch(() => img.replaceWith(
          el("span", { class: "faint small" }, `🖼 ${name} (unavailable)`)));
      row.append(img);
    } else {
      const btn = el("button", { class: "btn small att-file", title: rel }, `📎 ${name}`);
      btn.onclick = () => apiBlobUrl(fileUrl(rel))
        .then((f) => { blobs?.push(f.url); openInTab(f, name, blobs); })
        .catch((err) => toast(`could not load ${name}: ${err.message}`, 4000, { error: true }));
      row.append(btn);
    }
  }
  return row;
}

/** Revoke and forget every object URL in an owner's array. */
export function revokeBlobs(blobs) {
  for (const url of blobs.splice(0)) URL.revokeObjectURL(url);
}
