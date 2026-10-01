// Form persistence: text typed into the web UI survives a page refresh (and quick tab
// switches) until it is saved or the tab is closed. sessionStorage-backed, keyed by the
// view's hash-path + a stable field key — so the same field on the same view restores, but
// unrelated views never collide. Global: installed once from app.js,
// it uses event delegation to capture edits and a MutationObserver to restore values as
// views (re)render. It only restores fields that mount EMPTY, so a server-loaded value is
// never clobbered by stale draft text — the case it heals is "I typed into a blank field
// and refreshed / navigated away". Storage goes through util.js's `session`, which degrades to
// memory where the browser refuses it (drafts then last as long as the page).

import { session } from "/static/util.js";

const PREFIX = "rsched.formpersist.";
const SEL = "input, textarea, select";

// Fields we must never remember: non-text controls, anything explicitly opted out with
// data-nopersist, and — DENY-BY-DEFAULT — anything credential-shaped. An opt-out per field is
// a guard the next form forgets: the Secrets value box (a masked TEXTAREA, so a type check
// never saw it) and the proxy sign-in paste box (an OAuth code + state) both kept every
// keystroke in the tab's storage until each was opted out by hand.
function skip(node) {
  if (!node.matches || !node.matches(SEL)) return true;
  if (node.type === "file" || node.type === "hidden" || node.type === "checkbox"
      || node.type === "radio" || node.type === "submit" || node.type === "button") return true;
  if (node.hasAttribute("data-nopersist") || credentialShaped(node)) return true;
  return !fieldKey(node);
}

// Words that make a field read as a credential. Matched against what DESCRIBES the field
// (name, id, placeholder, aria-label) — never against an explicit `data-persist` key, which is
// the author's own statement that the field holds a draft worth keeping.
const CREDENTIAL_WORDS = /token|secret|password|key|pass|code/i;
const CREDENTIAL_AUTOCOMPLETE = new Set(["off", "new-password", "current-password",
                                         "one-time-code"]);

function credentialShaped(node) {
  if (node.type === "password" || node.hasAttribute("data-secret")) return true;
  if (CREDENTIAL_AUTOCOMPLETE.has((node.getAttribute("autocomplete") || "").toLowerCase()))
    return true;
  const masked = node.style && node.style.webkitTextSecurity;      // a masked textarea
  if (masked && masked !== "none") return true;
  if (node.hasAttribute("data-persist")) return false;
  return [node.id, node.getAttribute("name"), node.getAttribute("placeholder"),
          node.getAttribute("aria-label")].some((s) => s && CREDENTIAL_WORDS.test(s));
}

// A stable identifier for a field within its view: explicit id/name/data-persist win;
// otherwise fall back to placeholder — disambiguated by document position when several
// same-tag fields share one placeholder (e.g. the identical inputs on every endpoint /
// model card), so their drafts never bleed into each other. A lone field keeps the plain
// "ph:" key, so the common single-field case's saved drafts stay stable.
function fieldKey(node) {
  const explicit = node.getAttribute("data-persist") || node.id || node.getAttribute("name");
  if (explicit) return explicit;
  const ph = node.getAttribute("placeholder");
  if (!ph) return "";
  const same = [...document.querySelectorAll(node.tagName)]
    .filter((n) => n.getAttribute("placeholder") === ph);
  return same.length > 1 ? `ph:${ph}#${same.indexOf(node)}` : "ph:" + ph;
}

function viewKey() {
  // The path part of the hash only — a field's draft belongs to a view, not a query string.
  return (location.hash || "#/").split("?")[0];
}

function storeKey(node) {
  return PREFIX + viewKey() + "::" + fieldKey(node);
}

function save(node) {
  if (skip(node)) return;
  const k = storeKey(node);
  const v = node.value;
  if (v === "" || v == null) session.remove(k);
  else session.set(k, v);
}

function restore(node) {
  if (skip(node)) return;
  // Only fill fields that mount empty — never overwrite a value the view loaded itself.
  if (node.value !== "" && node.value != null) return;
  const v = session.get(storeKey(node));
  if (v != null && v !== "") node.value = v;
}

function restoreTree(root) {
  if (!root || !root.querySelectorAll) return;
  if (root.matches && root.matches(SEL)) restore(root);
  root.querySelectorAll(SEL).forEach(restore);
}

// Forget a field's saved draft — views call this right after the field's content was
// successfully SUBMITTED, so a later render or reload never refills text the server
// already has (submitted content must not come back as a draft).
export function forgetField(node) {
  session.remove(storeKey(node));
}

export function installFormPersistence() {
  // Capture edits everywhere via delegation (works for nodes added later).
  document.addEventListener("input", (e) => { if (e.target) save(e.target); }, true);
  document.addEventListener("change", (e) => { if (e.target) save(e.target); }, true);

  // Restore as views render into #view (and on first load).
  const mount = document.getElementById("view") || document.body;
  const obs = new MutationObserver((records) => {
    for (const r of records) {
      r.addedNodes.forEach((n) => { if (n.nodeType === 1) restoreTree(n); });
    }
  });
  obs.observe(mount, { childList: true, subtree: true });
  restoreTree(mount);
}
