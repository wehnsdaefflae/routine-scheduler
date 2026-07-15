// Form persistence: text typed into the web UI survives a page refresh (and quick tab
// switches) until it is saved or the tab is closed. sessionStorage-backed, keyed by the
// view's hash-path + a stable field key — so the same field on the same view restores, but
// unrelated views never collide. Global and dependency-free: installed once from app.js,
// it uses event delegation to capture edits and a MutationObserver to restore values as
// views (re)render. It only restores fields that mount EMPTY, so a server-loaded value is
// never clobbered by stale draft text — the case it heals is "I typed into a blank field
// and refreshed / navigated away".

const PREFIX = "rsched.formpersist.";
const SEL = "input, textarea, select";

// Fields we must never remember: passwords/tokens, file pickers, buttons, and anything
// explicitly opted out with data-nopersist.
function skip(node) {
  if (!node.matches || !node.matches(SEL)) return true;
  if (node.type === "password" || node.type === "file" || node.type === "hidden"
      || node.type === "checkbox" || node.type === "radio" || node.type === "submit"
      || node.type === "button") return true;
  if (node.hasAttribute("data-nopersist")) return true;
  const key = fieldKey(node);
  return !key || /token|secret|password/i.test(key);
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
  try {
    const k = storeKey(node);
    const v = node.value;
    if (v === "" || v == null) sessionStorage.removeItem(k);
    else sessionStorage.setItem(k, v);
  } catch { /* storage full / disabled — persistence is best-effort */ }
}

function restore(node) {
  if (skip(node)) return;
  // Only fill fields that mount empty — never overwrite a value the view loaded itself.
  if (node.value !== "" && node.value != null) return;
  try {
    const v = sessionStorage.getItem(storeKey(node));
    if (v != null && v !== "") node.value = v;
  } catch { /* ignore */ }
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
  try { sessionStorage.removeItem(storeKey(node)); } catch { /* ignore */ }
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
