// The scope editor on a scoped provider (Settings → Connections, 2026-11-09). A provider's
// consent scopes lived in a write-only Secrets entry (GOOGLE_OAUTH_SCOPES), so changing one meant
// retyping ~48 URLs blind on one card and clicking reconnect on another. Here the list is SHOWN,
// grouped by the API each scope's own URL names, edited in place, counted against the provider's
// consent ceiling, and compared with what each connected account was actually GRANTED — saving
// changes no token, only a reconnect does, so the gap between the two is the thing to see.
// The server owns every rule (`oauth/scopes.py`): it normalizes, refuses and warns; this renders.

import { api } from "/static/api.js";
import { act, chip, el, toast } from "/static/util.js";

const BARE = new Set(["openid", "email", "profile"]);

/** `drive.readonly` → the provider's full scope URL, mirroring `oauth/scopes.normalize`. */
function normalize(p, raw) {
  const s = raw.trim();
  return (!p.scope_base || !s || s.includes("://") || BARE.has(s)) ? s : p.scope_base + s;
}

/** The API a scope belongs to, read off its URL (`…/auth/gmail.modify` → `gmail`). */
function apiOf(scope) {
  return BARE.has(scope) ? "openid" : scope.replace(/\/+$/, "").split("/").pop().split(".")[0];
}

const short = (p, s) => (p.scope_base && s.startsWith(p.scope_base) ? s.slice(p.scope_base.length) : s);

/** One provider's editor. `connections` are this provider's connected accounts; `reconnect`
 *  starts the consent for one of them (the view's own flow). */
export function scopeEditor(p, connections, { reload, reconnect }) {
  let list = [...(p.requested || [])];
  const box = el("div", { class: "panel", style: "margin:4px 0 10px 82px", "data-scope-editor": p.id });

  function render() {
    const over = p.max_scopes && list.length > p.max_scopes;
    const groups = {};
    for (const s of list) (groups[apiOf(s)] ||= []).push(s);
    const head = el("div", { class: "row small" },
      el("span", { "data-scope-count": "", style: over ? "color:var(--err);font-weight:600" : "" },
        `${list.length}${p.max_scopes ? ` / ${p.max_scopes}` : ""} scopes`),
      p.scope_docs ? el("a", { href: p.scope_docs, target: "_blank", rel: "noopener" },
        "Google's scope list ↗") : null,
      p.creds_from ? el("span", { class: "muted" }, `uses the ${p.creds_from} OAuth app`) : null,
      // The Cloud console, where a scope's API is ENABLED — a granted scope is useless until it is
      // (Docs answered SERVICE_DISABLED with its scope on the token, 2026-11-10).
      p.console?.enabled_apis ? el("a", { href: p.console.enabled_apis, target: "_blank",
        rel: "noopener", "data-console": "apis" }, "enabled APIs ↗") : null,
      p.console?.consent ? el("a", { href: p.console.consent, target: "_blank", rel: "noopener",
        "data-console": "consent" }, "consent screen ↗") : null);
    const rule = p.scope_prefixes?.length
      ? el("div", { class: "muted small" }, `only ${p.scope_prefixes.map((x) => short(p, x)).join(", ")}…`
        + " — the API refuses a token carrying anything else") : null;
    const rows = Object.keys(groups).sort().map((api_) => el("div", { class: "row small",
      style: "flex-wrap:wrap;gap:4px;margin:3px 0", "data-scope-group": api_ },
      el("span", { style: "width:110px;font-weight:600" }, api_),
      p.console?.api?.[api_] ? el("a", { href: p.console.api[api_], target: "_blank",
        rel: "noopener", class: "small", "data-console-api": api_,
        title: `find the ${api_} API in your Cloud project — enable it there` }, "console ↗") : null,
      ...groups[api_].map((s) => {
        const x = el("button", { class: "btn small ghost", title: `remove ${s}`,
          "data-scope-remove": s }, "×");
        x.onclick = () => { list = list.filter((v) => v !== s); render(); };
        return el("span", { class: "chip", "data-scope": s }, short(p, s), x);
      })));
    const add = el("input", { type: "text", style: "flex:1", "data-nopersist": true,
      placeholder: p.scope_base ? "drive.readonly, or a full scope URL" : "scope" });
    const addBtn = el("button", { class: "btn small" }, "add");
    const doAdd = () => {
      const fresh = add.value.split(/[\s,]+/).map((v) => normalize(p, v)).filter(Boolean);
      list = [...new Set([...list, ...fresh])];
      render();
    };
    addBtn.onclick = doAdd;
    add.onkeydown = (e) => { if (e.key === "Enter") doAdd(); };
    // What each connected account was GRANTED, against the list as edited: a scope only arrives
    // with a reconnect, and one no longer requested stays on the token until then.
    const granted = connections.map((c) => {
      const have = new Set(c.scopes || []);
      const missing = list.filter((s) => !have.has(s));
      const extra = [...have].filter((s) => !list.includes(s));
      const said = !missing.length && !extra.length ? "granted exactly this list"
        : [missing.length ? `${missing.length} not granted yet (reconnect)` : "",
           extra.length ? `${extra.length} granted but no longer requested` : ""]
          .filter(Boolean).join(" · ");
      return el("div", { class: "small", "data-scope-account": c.account },
        el("span", { style: "font-weight:600" }, `${p.id}:${c.account}`), " — ",
        chip(said, missing.length || extra.length ? "partial" : "ok"));
    });
    const save = (andReconnect) => async () => {
      const r = await api(`/api/settings/oauth/${p.id}/scopes`, { method: "PUT", body: { scopes: list } });
      for (const w of r.warnings || []) toast(w, 6000);
      if (andReconnect) await reconnect(p.id, andReconnect);
      else reload();
    };
    const saveBtn = el("button", { class: "btn small primary", "data-scope-save": "" }, "save scopes");
    saveBtn.disabled = Boolean(over) || !list.length;
    saveBtn.onclick = () => act(saveBtn, save(null), "scopes saved — reconnect to apply them");
    const reconnectBtns = connections.map((c) => {
      const b = el("button", { class: "btn small", "data-scope-save-reconnect": c.account },
        `save & reconnect ${c.account}`);
      b.disabled = saveBtn.disabled;
      b.onclick = () => act(b, save(c.account));
      return b;
    });
    box.replaceChildren(head, rule, ...rows,
      ...(p.scope_errors || []).map((e) => el("div", { class: "small", style: "color:var(--err)" }, e)),
      ...(p.scope_warnings || []).map((w) => el("div", { class: "small", style: "color:var(--warn)" }, w)),
      el("div", { class: "row mt" }, add, addBtn),
      ...granted,
      el("div", { class: "row mt" }, saveBtn, ...reconnectBtns));
  }
  render();
  return box;
}
