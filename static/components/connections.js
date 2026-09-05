// The OAuth-connection binding card (D55): bind one connected account per provider so the
// engine injects its access token (e.g. GOOGLE_ACCESS_TOKEN, NOTION_ACCESS_TOKEN) into any util
// that declares it. Shared by the routine config page AND the conversation header — a
// conversation is routine-shaped, so it binds connections exactly like a routine (the engine's
// token injection reads routine.yaml `connections:` either way). The catalog of connectable
// accounts comes from Settings → Connections (/api/settings/oauth); `bound` is the current
// {provider: account} map; `onSave` receives the new map and PATCHes the owner.

import { api } from "/static/api.js";
import { el, skeleton, toast } from "/static/util.js";

export function connectionsCard(bound, { onSave, onChange } = {}) {
  // Two modes. With `onSave` it is the routine/domain editor: a save button PATCHes the
  // binding. With `onChange` it is a PRE-START picker (F339, the conversation composer):
  // no save button — every change reports the whole {provider: account} map to the caller,
  // which submits it with the rest of the form.
  const selects = {};
  const current = () => {
    const connections = {};
    for (const [pid, sel] of Object.entries(selects)) if (sel.value) connections[pid] = sel.value;
    return connections;
  };
  const box = el("div", { class: "panel" }, skeleton(["50%"]));
  api("/api/settings/oauth").then((oauth) => {
    box.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:8px" },
      "Bind an OAuth account per provider — its access token is injected into utils that "
      + "declare it (e.g. NOTION_ACCESS_TOKEN). Connect accounts in ",
      el("a", { href: "#/settings?section=connections" }, "Settings → Connections"), "."));
    const byProvider = {};
    for (const c of (oauth.connections || [])) (byProvider[c.provider] ||= []).push(c.account);
    for (const p of (oauth.providers || [])) {
      const accounts = byProvider[p.id] || [];
      const sel = el("select", {}, [el("option", { value: "" }, "— none —"),
        ...accounts.map((a) => el("option", { value: a }, a))]);
      sel.value = (bound || {})[p.id] || "";
      selects[p.id] = sel;
      if (onChange) sel.onchange = () => onChange(current());
      box.append(el("div", { class: "row", style: "margin:5px 0", "data-conn-row": p.id },
        el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, p.name),
        accounts.length ? sel
          : el("span", { class: "muted small" }, "no connected accounts — connect one in Settings")));
    }
    if (onSave) {
      box.append(el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: async () => {
          try { await onSave(current()); toast("connections saved"); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save connections")));
    }
  }).catch((err) => box.replaceChildren(el("div", { class: "muted" }, err.message)));
  return box;
}
