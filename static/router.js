// Hash routing helpers. The hash carries BOTH the path and query state, e.g.
//   #/log?routine=x&status=running   or   #/run/slug:ts?sub=2
// The route table matches the PATH; views read their state from the query and write it back
// with setQuery() (history.replaceState — no hashchange, no re-render), so filters / selection
// survive reload and are shareable, without tearing the view down. Real navigation between
// views goes through navigate()/location.hash (a pushState-equivalent) so Back works.

export function parseHash(hash = location.hash) {
  const h = hash || "#/";
  const qi = h.indexOf("?");
  const path = (qi === -1 ? h : h.slice(0, qi)) || "#/";
  const query = {};
  if (qi !== -1) for (const [k, v] of new URLSearchParams(h.slice(qi + 1))) query[k] = v;
  return { path, query };
}

// Drop empty / null / undefined values so the URL stays clean (a blank filter = absent key).
export function buildHash(path, query = {}) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query))
    if (v !== "" && v !== null && v !== undefined) params.set(k, v);
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

// Merge `updates` into the current query and rewrite the URL in place. By default this uses
// replaceState (silent — the current view keeps its DOM); pass {push:true} to make it a real
// navigation (fires hashchange → re-render). A value of "" / null / undefined drops the key.
export function setQuery(updates, { push = false } = {}) {
  const { path, query } = parseHash();
  const next = { ...query, ...updates };
  const hash = buildHash(path, next);
  if (push) { location.hash = hash; return; }
  history.replaceState(history.state, "", location.pathname + location.search + hash);
}

// Navigate to another view (pushes a history entry so Back returns here).
export function navigate(path, query = {}) {
  location.hash = buildHash(path, query);
}

// Re-render the CURRENT view in place (teardown + fresh mount) without a full page
// reload — the SPA analog of location.reload(). route() ignores the event detail and
// re-reads location.hash, so a synthetic hashchange is exactly a remount.
export function remount() {
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

// Rewrite the whole hash (path + query) in place — no hashchange, no re-render. For a view that
// wants its deep state (e.g. an open editor) addressable without tearing itself down.
export function replaceHash(path, query = {}) {
  history.replaceState(history.state, "", location.pathname + location.search + buildHash(path, query));
}
