// A HAND-BACK, rendered as what it is: a child's result landing in this conversation, not the
// person speaking. `branches.hand_back` and the detached-task delivery both file an inbox
// message whose `via` says which channel it came from, and the engine records it as a
// `user_injection` carrying that `via` — so the chat used to render a branch's conclusion as a
// USER bubble, in the operator's own voice, with the copied files named only inside the prose.
//
// ONE card for both channels, keyed on `via` (`branch` and `background`), because the two
// deliveries differ in wording and in where the link goes, not in shape.
//
// The landed files come from the record's `extra` (inbox.file_message → `handback`), which is
// the paths `child.collect_handback` returned: the renderer never parses them out of the text.
// Each one is a button, not an anchor — the file route needs the console's auth header, so the
// artifacts panel fetches it (components/artifacts.js `openPath`), exactly as a click on its
// own row does.

import { el } from "/static/util.js";
import { md } from "/static/md.js";

//: via → how the card introduces itself. A via with no entry here is not a hand-back.
const CHANNELS = {
  branch: {
    noun: "branch",
    href: (slug) => `#/conversations/${slug}`,
    hint: "a branch handed its result back — its conversation stays its own, nothing was merged",
  },
  background: {
    noun: "background task",
    href: () => "",
    hint: "a detached background task finished and handed its result back",
  },
};

export const isHandback = (via) => Object.hasOwn(CHANNELS, String(via || ""));

// The hand-back prose carries a headline line and a "Collected into your artifacts/: …" line,
// both of which the card itself now says — so the body shows what is left: the child's own
// summary. Split on the blank lines the one hand-back wording (engine/child.handback_text)
// builds the message from, never on a guess about the sentences.
function summaryOf(text) {
  const parts = String(text || "").split("\n\n").map((p) => p.trim()).filter(Boolean);
  return parts.slice(1).filter((p) => !p.startsWith("Collected into your artifacts/:")).join("\n\n");
}

/** The card for ONE delivered hand-back, or null when `via` names no hand-back channel.
 *
 * `p` is the `user_injection` payload: `via`, `text`, and the `handback` record the inbox
 * message carried ({name, slug, paths}). `openArtifact(path)` opens a landed file in the
 * artifacts panel; without it the files render as plain names, which is what a transcript
 * replay outside a conversation view gets.
 */
export function handbackCard(p, { openArtifact } = {}) {
  const channel = CHANNELS[String(p.via || "")];
  if (!channel) return null;
  const rec = p.handback || {};
  const name = String(rec.name || rec.slug || "");
  const href = channel.href(String(rec.slug || ""));
  const label = name
    ? el("span", {}, `${channel.noun} “`,
        href ? el("a", { href }, name) : el("span", {}, name), "” handed back")
    : el("span", {}, `a ${channel.noun} handed back`);
  const paths = (rec.paths || []).map(String);
  const files = paths.length
    ? el("div", { class: "msg-attach" }, ...paths.map((path) => el("button", {
        type: "button", class: "attach-chip", title: path,
        onclick: () => openArtifact?.(path),
      }, "📎 ", path.split("/").pop())))
    : null;
  const summary = summaryOf(p.text);
  return el("div", { class: "ev subrun handback-card" },
    el("div", { class: "evlabel" }, "↩ ", label),
    summary ? el("div", { class: "msg-body" }, md(summary)) : null,
    files,
    el("div", { class: "faint small" }, channel.hint));
}
