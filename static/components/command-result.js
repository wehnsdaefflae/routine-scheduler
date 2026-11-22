// One visible block per executed USER COMMAND — the slash command a person typed into a
// conversation, whose result is the answer they asked for and must not fold away with the
// model's own tool work. Compact per-kind rendering of the observation payload; the full
// record stays in the transcript.
//
// Extracted from components/chat.js, which renders the conversation itself: a command result is
// a rendering of one observation shape per action kind, it grows with the action schema, and
// chat.js was at the house size ceiling. The chat still owns WHEN it is shown (the
// `user_command` observation branch); this owns what it looks like.

import { el, fullOutput, compressionInfo } from "/static/util.js";

export function commandResult(p) {
  let body;
  if (p.error) body = `✕ ${p.error}`;
  else if (p.kind === "util") {
    body = (p.stdout || "(no stdout)") + (p.stderr ? `\n[stderr]\n${p.stderr}` : "")
      + fullOutput(p.full_output) + compressionInfo(p.compression);
    if (p.listing != null) body = p.listing;
    if (p.source != null) body = p.source;
  } else if (p.kind === "read_file") {
    body = p.files
      ? p.files.map((f) => `--- ${f.path}\n${f.error || f.content || ""}`).join("\n\n")
      : (p.content ?? "");
  } else if (p.kind === "view_image") {
    // The THUMBNAIL for an image the run looked at lives in the transcript renderer's own
    // observation branch (components/transcript.js, F559) — that is what draws a
    // conversation's observations. THIS branch renders a USER-TYPED command's result, where
    // the paths and any vision description are the whole answer.
    body = (p.files || []).map((f) => f.error ? `${f.path}: ${f.error}`
      : f.text ? `${f.path}:\n${f.text}` : f.path).join("\n\n");
  } else if (p.kind === "write_file") body = `wrote ${p.bytes} bytes to ${p.path}`;
  else if (p.kind === "edit_file") body = `replaced ${p.replacements} occurrence(s) in ${p.path}`;
  else if (p.kind === "llm") body = p.reply || "";
  else if (p.kind === "memory_read") body = p.missing ? `no note ${p.name}` : (p.content || "");
  else if (p.kind === "memory_write") body = `note ${p.name}.md ${p.deleted ? "deleted" : p.created ? "created" : "revised"}`;
  else body = JSON.stringify(p, null, 1);
  return el("div", { class: `ev cmd-result${p.error ? " err" : ""}` },
    el("pre", {}, String(body)));
}
