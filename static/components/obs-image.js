// What a `view_image` ROW shows: the file under the ACTION, and the image's answer in the
// RESULT. The two halves of R3357, asked for by the operator during run
// bahnbonus-seat-position:20261111-210421 (turn 29).
//
// What he was looking at: the action element showed only the narration, and the result element
// showed `--- state/ice1003_formation.png (image/png) — shown to you below; look at it now.`
// — a file reference standing where the answer should be. His two sentences:
//
//   1. "The link to the image belongs in the message's `action` element — it is part of what
//      was DONE (which file was opened), not part of what came back."
//   2. "The `result` element should contain the answer to the model's query about the image."
//
// POINT 1 is a move, and it also FIXES something: the thumbnails lived inside the result's
// collapsed <details> (F559), so the image a run looked at was invisible until the reader
// expanded the fold — and `BRIEF_FIELD.view_image` is `path`, so a `view_image paths=[…]`
// batch rendered its action brief EMPTY. `imageLinks` reads both fields and puts the files
// where the action is, always visible, each one opening through the authenticated blob route
// (components/obs-attachments.js owns that route and the lifetime of what it hands back —
// nothing here is a second implementation of it).
//
// POINT 2 IS BOUNDED BY WHAT THE ENGINE RECORDS, and this module does not pretend otherwise.
// Two paths produce genuinely different answers (engine/obs_files.format_files, the per-file
// flags it writes):
//
//   • the VISION-UTIL path (`via: "vision-util"`, with `text`) — this run's model cannot see
//     images, so engine/mediaops.py sent the file to the `vision` util and the util's returned
//     description IS what the run reasoned about. That text is a real answer and the result row
//     says so, naming the util as its author.
//   • the DIRECT path (`native: true`) — the image is attached to the prompt and shown to a
//     multimodal model, so the "answer" is whatever that model then says, which arrives in its
//     NEXT turn and is nowhere in this observation. Nothing may be invented into the result
//     row. What is honest, and what this renders, is a LABELLED pointer: the row says the image
//     was shown directly and that its reading is the following turn, and the pointer lands the
//     reader there (`data-turn-link`, the one turn-addressing mechanism — components/
//     turnlink.js; components/runrail.js already wires the whole run view, so this needs no new
//     plumbing in views/run.js).
//
// The fuller version the operator described — the model's reading shown IN the view_image row
// itself — needs the ENGINE to record that reading against this row. That is an engine change,
// deliberately not guessed at here; this module is the renderer half, and it says which of the
// two paths produced what the reader is looking at rather than flattening them into one word.

import { attachmentRow } from "/static/components/obs-attachments.js";
import { el } from "/static/util.js";

/** The files a `view_image` ACTION opened, as the action element's own row: the paths as text,
 *  and each file openable through the authenticated route (a thumbnail for an image, a chip
 *  otherwise). Reads `paths` as well as `path`, which is why a batch no longer renders blank.
 *
 *  NEVER null — what the action element shows for a `view_image` is this module's whole
 *  responsibility, so the fallbacks live here rather than in the renderer that is frozen at its
 *  size ceiling: `brief` where the action names no file at all, names-only where the mount
 *  serves no files (a conversation's work fold, a transcript mounted without `fileUrl`). */
export function imageLinks(a, fileUrl, blobs, brief = "") {
  const rels = Array.isArray(a.paths) && a.paths.length
    ? a.paths : a.path ? [String(a.path)] : [];
  if (!rels.length) return el("span", { class: "muted" }, brief);
  const names = el("span", { class: "muted" }, rels.join(", ").slice(0, 200));
  const files = attachmentRow(rels, fileUrl, blobs);
  return files ? el("span", { class: "img-links" }, names, files) : names;
}

/** The DIRECT path's labelled pointer, or null when the observation carries its own answer
 *  (the vision util's description), when nothing was shown (an error, a resume replay) or when
 *  the turn is unknown. A button rather than a link: the turn is addressed by attribute, and an
 *  href guessing a run id would be a dead link wherever this renderer is mounted without one. */
export function imageAnswer(o, ev) {
  const files = o.files || [];
  const direct = files.some((f) => f.native && !f.error);
  if (!direct || o.media_replayed) return null;
  const turn = Number(ev?.turn);
  if (!Number.isFinite(turn)) return null;
  const next = turn + 1;
  return el("button", { class: "obs-answer-link", "data-turn-link": String(next),
    title: `the model was shown this image directly — jump to turn ${next}, where it says `
      + "what it read off it" },
    `↳ shown directly to the model — its reading is turn ${next}`);
}
