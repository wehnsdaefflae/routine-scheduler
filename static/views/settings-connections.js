// Settings -> OAuth connections (external accounts) - split from settings.js (one section per module; settings.js keeps
// the section order, nav, and deep-link jump). Appends its own panel and returns the fill
// promise so settings.js can await all sections before the anchor jump.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, skeleton, toast } from "/static/util.js";

export function renderConnections(view) {
  // -- OAuth connections (external accounts routines act on behalf of) --------------
  const connBox = el("div", { class: "panel" });
  connBox.append(skeleton(["60%", "85%"]));
  view.append(connBox);
  async function fill() {
    let d;
    try { d = await api("/api/settings/oauth"); }
    catch (err) { connBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    connBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
      "Connect external accounts (e.g. Notion) via OAuth so routines can act on your behalf. Bind a ",
      "connection on a routine's page; its access token is injected only into utils that declare it."));

    function disconnectBtn(provider, account) {
      const b = el("button", { class: "btn small danger" }, "disconnect");
      b.onclick = async () => {
        if (!(await confirmDialog(`Disconnect ${provider}:${account}?`, { confirmLabel: "disconnect" }))) return;
        try { await api(`/api/settings/oauth/${provider}/${encodeURIComponent(account)}`, { method: "DELETE" }); fill(); }
        catch (err) { toast(err.message, 4000, { error: true }); }
      };
      return b;
    }

    // Auth-code + PKCE: authorize opens in a NEW tab (the provider redirects it to /oauth/callback);
    // this tab polls the flow until the callback reports connected.
    async function startConnect(providerId, account) {
      if (!account) { toast("enter an account label first"); return; }
      let f;
      try { f = await api(`/api/settings/oauth/${providerId}/authorize-start`, { method: "POST", body: { account } }); }
      catch (err) { toast(err.message, 6000, { error: true }); return; }
      window.open(f.authorize_url, "_blank", "noopener");
      toast("authorize in the new tab, then return here");
      const deadline = Date.now() + 600 * 1000;
      const tick = async () => {
        if (Date.now() > deadline) return;
        let p;
        try { p = await api(`/api/settings/oauth/flow/${f.flow_id}`); } catch { return; }
        if (p.status === "connected") { toast(`${providerId} connected`); fill(); return; }
        if (p.status === "error") { toast(`✗ ${p.error || "authorization failed"}`, 6000, { error: true }); return; }
        setTimeout(tick, 2000);
      };
      setTimeout(tick, 2000);
    }

    // Public URL (public_url) — the BASE the provider redirects back to; the callback is derived
    // (this field is the base, NOT the callback path — the app appends /oauth/callback itself).
    // Pre-fill from THIS browser's origin (the URL you reached the console at) when it's https and
    // nothing is saved yet — so you almost never have to type it.
    const originGuess = location.origin.startsWith("https://") ? location.origin : "";
    const urlIn = el("input", { type: "text", placeholder: "https://host.ts.net", style: "flex:1", value: d.public_url || originGuess });
    const urlSave = el("button", { class: "btn small" }, "save");
    urlSave.onclick = async () => {
      try { await api("/api/settings/oauth/public-url", { method: "PUT", body: { public_url: urlIn.value.trim() } }); toast("public URL saved"); fill(); }
      catch (err) { toast(err.message, 5000, { error: true }); }
    };
    const callbackLine = el("div", { class: "small mt" });
    if (d.public_url) {
      const cb = `${d.public_url.replace(/\/+$/, "")}/oauth/callback`;
      callbackLine.append(
        el("span", { class: "muted" }, "Register this exact callback in each provider's OAuth app: "),
        el("code", { "data-conn-callback": "", style: "user-select:all" }, cb),
        el("button", { class: "btn small", style: "margin-left:8px",
          onclick: () => { navigator.clipboard?.writeText(cb); toast("callback URL copied"); } }, "copy"));
    }
    connBox.append(
      el("div", { class: "mt small", style: "font-weight:600" }, "Public URL"),
      el("div", { class: "muted small" },
        "Your instance's external https BASE url (e.g. your Tailscale Serve URL) — the base, ",
        "not a path. Providers redirect back to ", el("code", {}, "<this>/oauth/callback"), "."),
      el("div", { class: "row mt", "data-conn-url": "" }, urlIn, urlSave),
      (!d.public_url && originGuess)
        ? el("div", { class: "muted small mt" }, "Pre-filled from this browser's address (",
            el("code", {}, originGuess), ") — click save to use it.")
        : null,
      callbackLine);

    // Providers — connect a new account (disabled until the redirect URL + the app creds are set).
    connBox.append(el("div", { class: "mt small", style: "font-weight:600" }, "Providers"));
    for (const p of d.providers) {
      const acct = el("input", { type: "text", placeholder: "account label", style: "width:150px" });
      const connect = el("button", { class: "btn small primary" }, "connect");
      connect.disabled = !(p.configured && d.public_url_set);
      connect.onclick = () => startConnect(p.id, acct.value.trim());
      // Straight link to where you create the OAuth app for this provider (its dev console).
      const consoleLink = p.console_url
        ? el("a", { href: p.console_url, target: "_blank", rel: "noopener", class: "small",
            "data-provider-link": p.id, title: `create the ${p.name} OAuth app` }, "create app ↗")
        : null;
      const status = p.configured
        ? el("span", { class: "small", style: "color:var(--ok)" }, "✓ app configured")
        : el("span", { class: "small", style: "color:var(--warn)" }, `set ${p.client_id_key} + secret in Secrets`);
      connBox.append(el("div", { class: "row", style: "margin:4px 0", "data-provider": p.id },
        el("span", { style: "width:82px;font-weight:600" }, p.name), consoleLink, status, acct, connect));
    }
    if (!d.public_url_set)
      connBox.append(el("div", { class: "muted small mt" }, "set the redirect URL above to enable connecting"));

    // Connected accounts.
    connBox.append(el("div", { class: "mt small", style: "font-weight:600" }, "Connected accounts"));
    if (!d.connections.length) connBox.append(el("div", { class: "muted small", "data-conn-empty": "" }, "none yet"));
    else connBox.append(el("div", { class: "tablewrap" }, el("table", { class: "list" }, el("tbody", {},
      d.connections.map((c) => el("tr", { "data-conn": `${c.provider}:${c.account}` },
        el("td", {}, `${c.provider}:${c.account}`),
        el("td", { class: "muted small" }, c.label || ""),
        el("td", { class: "small", style: `color:${c.needs_reauth ? "var(--warn)" : "var(--ok)"}` },
          c.needs_reauth ? "needs re-auth" : "ok"),
        el("td", {}, disconnectBtn(c.provider, c.account))))))));
  }
  return fill();
}
