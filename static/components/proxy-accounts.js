// The subscription-proxy ACCOUNT card: who the proxy is signed in as for one endpoint's
// models, and the way back in when that session dies.
//
// Its own module because it is a sign-in FLOW, not a settings field — a three-step consent
// dance with the provider, sitting inside an endpoint card that is otherwise a form. It was
// exported from settings-endpoints.js for no reason but that it happened to be written there;
// nothing outside that file imported it.

import { api } from "/static/api.js";
import { el, toast } from "/static/util.js";

//: Who the proxy is signed in as FOR THIS ENDPOINT'S MODELS, and the way back in when a session
//: dies. The proxy owns the OAuth flow; the console only ferries the consent link out and the
//: code back. The consent page redirects to localhost on the OPERATOR'S device, where nothing
//: listens, so that page fails to load — its address still carries the code, and pasting it
//: here finishes the sign-in (docs/claude-proxy-cutover.md). The rows and the sign-in
//: controls come filtered from the server by the provider of the models bound to this
//: endpoint: the Claude card shows the Claude account, the Codex card the Codex one.
const PROVIDER_LABEL = { claude: "Claude", anthropic: "Claude", codex: "Codex" };

export function proxyAccounts(ep, onSignedIn) {
  const base = `/api/settings/endpoints/${encodeURIComponent(ep.name)}`;
  const list = el("div", { class: "proxy-accounts" });
  const loginBox = el("div", {});
  const box = el("div", { class: "small", style: "margin-top:4px" });

  function accountLine(acc) {
    const bad = acc.disabled || acc.unavailable || !["", "ok", "active"].includes(acc.status);
    const word = acc.disabled ? "disabled"
      : acc.unavailable ? (acc.status_message || acc.status || "unavailable")
      : (acc.status || "ok");
    const retry = bad && acc.next_retry_after
      ? ` (retry after ${acc.next_retry_after.slice(0, 16).replace("T", " ")})` : "";
    return el("div", {},
      el("span", { style: `color:var(--${bad ? "err" : "ok"})` }, bad ? "✗ " : "✓ "),
      `${PROVIDER_LABEL[acc.provider] || acc.provider} ${acc.label || acc.name}: ${word}${retry}`);
  }

  async function load() {
    list.replaceChildren(el("span", { class: "muted" }, "proxy accounts: checking…"));
    let a;
    try { a = await api(`${base}/proxy-accounts`); } catch { list.replaceChildren(); return; }
    if (!a.supported) { list.replaceChildren(); return; }
    if (!a.ok) {
      list.replaceChildren(el("span", { class: "warn-line" }, `proxy accounts unavailable — ${a.error}`));
      return;
    }
    list.replaceChildren(...a.accounts.map(accountLine));
    if (!a.accounts.length) list.append(el("span", { class: "muted" }, "no proxy account signed in yet"));
    buttons.replaceChildren(...(a.providers || []).map(({ id, label }) => {
      const b = el("button", { class: "btn small" }, `re-authenticate ${label}`);
      b.onclick = () => startLogin(id, label);
      return b;
    }));
  }

  async function startLogin(provider, label) {
    loginBox.replaceChildren(el("div", { class: "muted" }, `asking the proxy for a ${label} sign-in link…`));
    let s;
    try { s = await api(`${base}/proxy-login`, { method: "POST", body: { provider } }); }
    catch (err) { loginBox.replaceChildren(el("div", { class: "warn-line" }, `✗ ${err.message}`)); return; }
    if (!s.ok) { loginBox.replaceChildren(el("div", { class: "warn-line" }, `✗ ${s.error}`)); return; }
    const paste = el("textarea", { rows: "2", style: "width:100%", "aria-label": `${label} sign-in response`,
      placeholder: `http://localhost:${s.callback_port}/callback?code=…&state=…   (or the code the page shows)` });
    const finish = el("button", { class: "btn small primary" }, "finish sign-in");
    const cancel = el("button", { class: "btn small" }, "cancel");
    const status = el("div", {});
    finish.onclick = async () => {
      if (!paste.value.trim()) { toast("paste the address or the code first"); return; }
      finish.disabled = true;
      status.replaceChildren(el("span", { class: "muted" }, "handing the code to the proxy…"));
      try {
        const r = await api(`${base}/proxy-login/complete`,
          { method: "POST", body: { provider, state: s.state, response: paste.value } });
        if (r.ok) {
          toast(`${label}: signed in through the proxy`);
          loginBox.replaceChildren();
          await load();
          if (onSignedIn) onSignedIn();
          return;
        }
        status.replaceChildren(el("span", { class: "warn-line" }, `✗ ${r.error}`));
      } catch (err) { status.replaceChildren(el("span", { class: "warn-line" }, `✗ ${err.message}`)); }
      finish.disabled = false;
    };
    cancel.onclick = () => loginBox.replaceChildren();
    loginBox.replaceChildren(el("div", { class: "panel mt" },
      el("div", {}, el("strong", {}, `${label} sign-in`), " — ",
        el("a", { href: s.url, target: "_blank", rel: "noopener" }, "1. open the sign-in page ↗"),
        " and finish consent there."),
      el("div", { class: "muted", style: "margin:4px 0" },
        `2. The sign-in page then sends this browser to localhost:${s.callback_port}, which exists `
        + "only on the server, so on this device that page fails to load. Copy its WHOLE address "
        + "from the address bar (it carries the code) and paste it below; if the page shows a code "
        + "instead, paste that."),
      paste, el("div", { class: "row" }, finish, cancel), status));
  }

  const buttons = el("div", { class: "row" });   // filled from the reply: this card's providers
  box.append(list, buttons, loginBox);
  load();
  return { box, reload: load };
}
