// Concept links for a message element — the util, rule, permission or reminder an action
// NAMES becomes a link to the page that holds it (operator, 2026-09-30: "in general i want all
// concepts in the message elements to by hyperlinked to their respective item: utils,
// permissions, rules, reminders, anything").
//
// The hard rule here, and the reason this is not a second reflinks.js: LINK FROM STRUCTURED
// FIELDS, NEVER FROM FREE TEXT. reflinks.js linkifies `F63`/`D14`/`R7` tokens by pattern and its
// own header warns that on arbitrary prose a bare "D1" is a false positive; a util called `job`
// or a rule called `work-order` would be far worse, matching ordinary English in every say line
// on the instance. So every function below takes an IDENTIFIER the action already carries in a
// field of its own — `a.name` for a util, `a.remind.id` for a reminder — and renders that one
// value as an anchor. Nothing is scanned.
//
// The destinations are the console's own routes (app.js): #/library/util/<name>,
// #/library/rule/<slug>, #/library/permission/<slug> open that item's editor directly, and a
// reminder is a routine's own state, so it goes to #/routine/<slug>, whose Health tab renders
// the local-reminder table with its tally (views/routine-health.js).

import { el } from "/static/util.js";

/** A util's name as a link to its library entry. */
export function utilLink(name, { cls = "concept-link" } = {}) {
  const text = String(name ?? "");
  if (!text) return text;
  return el("a", { class: cls, href: `#/library/util/${encodeURIComponent(text)}`,
                   title: `open the ${text} util in the library` }, text);
}

/** A general rule's slug as a link to its prose. */
export function ruleLink(slug, { cls = "concept-link" } = {}) {
  const text = String(slug ?? "");
  if (!text) return text;
  return el("a", { class: cls, href: `#/library/rule/${encodeURIComponent(text)}`,
                   title: `read the ${text} rule` }, text);
}

/** A conduct permission's slug as a link to its doc. */
export function permissionLink(slug, { cls = "concept-link" } = {}) {
  const text = String(slug ?? "");
  if (!text) return text;
  return el("a", { class: cls, href: `#/library/permission/${encodeURIComponent(text)}`,
                   title: `read the ${text} permission` }, text);
}

/** A reminder id as a link to the routine whose Health tab carries it and its tally. Without a
 *  slug there is nowhere to send the reader, so the id renders as the plain code it is —
 *  a dead link is worse than dead text. */
export function reminderLink(rid, slug, { cls = "concept-link" } = {}) {
  const text = String(rid ?? "");
  if (!text) return text;
  if (!slug) return el("code", { class: "rem-id" }, text);
  return el("a", { class: cls, href: `#/routine/${encodeURIComponent(String(slug))}`,
                   title: `${text} — see its pattern and tally on ${slug}'s health tab` },
    el("code", { class: "rem-id" }, text));
}
