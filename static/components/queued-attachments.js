// A QUEUED message's attachments on the global Messages page, and the object URLs they hold
// open (D275).
//
// Its own module because `static/views/messages.js` is over the repo's 350-line house bar and
// frozen at its ratchet ceiling (CLAUDE.md Standards, tests/test_file_size.py) — its own breach
// message says it: put the addition in its own file. Paying for a render by compacting prose in
// a 374-line view is not paying for it.
//
// What this owns is the LIFETIME, which is why it is a module and not two lines. That page
// reloads its whole queue on every submit, edit, withdrawal and filter change, and each pass
// creates one object URL per thumbnail: a decoded image stays in memory until its URL is
// revoked, so a pass that does not revoke the one before it leaks every image the operator
// has looked at. `newPass` is therefore the seam the view's own reload passes through — it
// revokes the previous pass and hands the rows straight back, so there is no second call for
// a future edit to forget.

import { attachmentRow } from "/static/components/obs-attachments.js";
import { revokeBlobs } from "/static/components/obs-attachments.js";

const blobs = [];

/** The previous pass's object URLs are dead; `rows` straight back, for the caller's `const`. */
export function newPass(rows) {
  revokeBlobs(blobs);
  return rows;
}

/** One queued message's attachment row, or null — fetched from THAT routine's own dir. */
export function queuedAttachmentRow(msg, slug) {
  return attachmentRow(msg.attachments, (rel) =>
    `/api/routines/${slug}/attachment?path=${encodeURIComponent(rel)}`, blobs);
}

/** Revoke and forget everything this component is holding — the view's teardown. */
export function revokeQueued() {
  revokeBlobs(blobs);
}
