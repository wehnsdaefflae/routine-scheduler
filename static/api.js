// Fetch + SSE wrappers with bearer-token auth. The token lives in localStorage; when it is
// missing or rejected, an in-page gate overlay collects it and pending requests retry — no
// window.prompt, no lost navigation. SSE mints a short-lived ticket per connection (EventSource has no
// headers).

import { storage } from "/static/util.js";

const KEY = "rsched_token";

export function getToken() {
  return (storage.get(KEY) || "").trim();
}

export function clearToken() {
  storage.remove(KEY);
  dropWorkerToken();
}

// ---- the service worker's copy of the token --------------------------------------------------
// A service worker cannot read localStorage, and sw.js has exactly one request to authenticate
// on its own: re-registering the push subscription when the browser rotates it. The Cache API is
// the only store both sides reach. THESE TWO LITERALS ARE PAIRED WITH static/sw.js — change them
// in one file only and push healing goes silently dead. Absent a secure context there is no
// `caches` at all; healing then waits for the next visit to Settings → Notifications.
const AUTH_CACHE = "rsched-auth";
const AUTH_ENTRY = "/__auth_token";

export async function syncWorkerToken() {
  const token = getToken();
  if (!token) return;
  try {
    const cache = await caches.open(AUTH_CACHE);
    await cache.put(AUTH_ENTRY, new Response(token));
  } catch { /* no Cache API here — the worker just cannot self-heal */ }
}

async function dropWorkerToken() {
  try { await caches.delete(AUTH_CACHE); } catch { /* nothing cached */ }
}

// ---- token gate: one overlay shared by every concurrent 401 --------------------------------
let gatePromise = null;

function requestToken(message) {
  if (gatePromise) return gatePromise;
  gatePromise = new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "token-gate";
    const panel = document.createElement("div");
    panel.className = "panel";
    const brand = document.createElement("div");
    brand.className = "tg-brand";
    const name = document.createElement("strong");
    name.textContent = "rsched";
    const sub = document.createElement("span");
    sub.className = "faint small";
    sub.textContent = "· access token required";
    brand.append(name, sub);
    const msg = document.createElement("div");
    msg.className = "tg-msg";
    msg.textContent = message;
    const hint = document.createElement("div");
    hint.className = "tg-msg faint";
    hint.textContent = "The token is in ~/.config/routine-scheduler/config.yaml on the server.";
    const input = document.createElement("input");
    input.type = "password";
    input.placeholder = "paste the API token";
    input.autocomplete = "off";
    const err = document.createElement("div");
    err.className = "tg-err";
    const btn = document.createElement("button");
    btn.className = "btn primary mt";
    btn.textContent = "connect";
    const submit = () => {
      const t = input.value.trim();
      if (!t) { err.textContent = "enter the token first"; return; }
      storage.set(KEY, t);
      syncWorkerToken();
      wrap.remove();
      gatePromise = null;
      resolve(t);
    };
    btn.addEventListener("click", submit);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
    panel.append(brand, msg, hint, input, err, btn);
    wrap.append(panel);
    document.body.append(wrap);
    input.focus();
  });
  return gatePromise;
}

// The one authed-fetch loop (token prompt + one 401 retry) both call shapes share.
async function authedJson(path, makeInit) {
  for (let attempt = 0; ; attempt++) {
    let token = getToken();
    if (!token) token = await requestToken("This console is token-protected. Sign in to continue.");
    const resp = await fetch(path, makeInit(token));
    // 401 = no/garbage credential. 403 + insufficient_scope (app.py, RFC 6750) = a VALID
    // token of the wrong TIER: the routine bearer reads everything, so the console renders
    // whole and only the first mutation fails — without re-gating here that browser can
    // never reach the token field again (no devtools on a phone). Other 403s are ordinary
    // refusals of an authorized caller and must fall through to the toast.
    const wrongToken = resp.status === 401
      || (resp.status === 403
          && (resp.headers.get("WWW-Authenticate") || "").includes("insufficient_scope"));
    if (wrongToken && attempt === 0) {
      clearToken();
      await requestToken(resp.status === 403
        ? "That is the routine token — read-only. Enter the operator's primary token."
        : "Token rejected — enter the current one.");
      continue;
    }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = new Error(data.detail || `${resp.status} ${resp.statusText}`);
      err.status = resp.status;
      throw err;
    }
    return data;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  return authedJson(path, (token) => ({
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }));
}

// Multipart upload (message attachments): same token/gate handling as api(), but the body
// is a FormData — the browser sets the multipart boundary, so no Content-Type of ours.
export async function apiUpload(path, formData, extraHeaders = {}) {
  return authedJson(path, (token) => ({
    method: "POST", headers: { Authorization: `Bearer ${token}`, ...extraHeaders }, body: formData }));
}

// Authenticated binary fetch → object URL, for content that renders via src attributes
// (iframes, images, PDFs) where no Authorization header can ride along. The caller owns
// the URL's lifetime (URL.revokeObjectURL when done).
export async function apiBlobUrl(path) {
  const resp = await fetch(path, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return { url: URL.createObjectURL(await resp.blob()), type: resp.headers.get("content-type") || "" };
}

// EventSource wrapper. Handlers are keyed by SSE event name; "onerror"/"onopen" are the
// EventSource callbacks. EventSource cannot send an Authorization header, and the bearer
// token in a query string would leak into access logs — so every connection first mints
// a SHORT-LIVED ticket (POST /api/sse-ticket) and sends that instead; reconnects (via
// stream.js liveTail, which re-invokes this) mint fresh tickets. Returns { close() } —
// usable before the connection is even up. Prefer liveTail for transcript tails.
// Concurrent open-EventSource gauge (F263). Browsers cap ~6 HTTP/1.1 connections per origin,
// and every EventSource holds one open for its whole life — so several live SSE streams (one
// per open run/conversation tail + the global bus) can approach the cap, at which point every
// new fetch (navigation, even the sse-ticket POST) stalls with NO error and NO main-thread long
// task: a NETWORK-stall "freeze" the F218 longtask observer structurally can't see. This gauge
// is stamped into `reconnect` and `freeze` traces so an audit can correlate a freeze with how
// many streams were open when it happened, instead of guessing.
let openStreams = 0;
export function openStreamCount() { return openStreams; }

export function sse(path, handlers) {
  let source = null;
  let closed = false;
  let counted = false;
  const release = () => { if (counted) { counted = false; openStreams -= 1; } };
  (async () => {
    let ticket;
    try { ticket = (await api("/api/sse-ticket", { method: "POST" })).ticket; }
    catch (err) { handlers.onerror?.(err); return; }
    if (closed) return;
    const sep = path.includes("?") ? "&" : "?";
    source = new EventSource(`${path}${sep}ticket=${encodeURIComponent(ticket)}`);
    counted = true; openStreams += 1;
    for (const [event, fn] of Object.entries(handlers)) {
      if (event === "onerror") source.onerror = fn;
      else if (event === "onopen") source.onopen = fn;
      else source.addEventListener(event, (e) => fn(JSON.parse(e.data)));
    }
  })();
  return { close: () => { closed = true; release(); source?.close(); } };
}
