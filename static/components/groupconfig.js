// The group's SHARED routine config (D82) — the block every member inherits, edited once
// here instead of N times across the members. Mounted as a collapsible section inside the
// group editor overlay (components/groupmanage.js).
//
// It deliberately reuses the ROUTINE page's own controls — permissionsPanel, rulePicker,
// rootsEditor, connectionsCard — so a group's permissions look and behave exactly like a
// routine's. A lookalike built here would drift from them the first time either changes.
//
// The group is a DEFAULT, not an override: lists union with the member's own, mappings merge
// per key with the member's value winning, and a key absent here is left entirely to each
// member (config/routine.py `apply_group_config`). Every control PATCHes the WHOLE config
// block, because the API replaces it wholesale — dropping a key returns that setting to the
// members.

import { api } from "/static/api.js";
import { connectionsCard } from "/static/components/connections.js";
import { rootsEditor } from "/static/components/fsroots.js";
import { permissionsPanel } from "/static/components/permissions.js";
import { rulePicker } from "/static/components/rulepicker.js";
import { el, toast } from "/static/util.js";

/** One labelled block: a heading, a one-line why, and the control. */
function block(title, hint, ...nodes) {
  return el("div", { class: "mt", "data-gcfg-block": title },
    el("div", { class: "small", style: "font-weight:600" }, title),
    ...(hint ? [el("div", { class: "muted small" }, hint)] : []),
    ...nodes);
}

/** The shared-config editor for one group.
 *  `g` is the group record (carrying `config` + `config_layers`), `save(config)` PATCHes it. */
export function groupConfigPanel(g, { save }) {
  const host = el("div", { class: "mt", "data-group-config": g.id });
  const cfg = () => ({ ...(g.config || {}) });
  // Write one key of the shared block. An empty list/map is DROPPED rather than stored, so
  // "no group value" and "an explicitly empty group value" cannot diverge.
  const put = async (key, value) => {
    const next = cfg();
    if (value == null || (Array.isArray(value) ? !value.length : !Object.keys(value).length)) {
      delete next[key];
    } else {
      next[key] = value;
    }
    await save(next);
  };

  host.append(el("div", { class: "muted small" },
    "Every member inherits this. A member's own routine.yaml still wins wherever it sets the "
    + "same key — lists add together, and a value here fills in only what the member leaves "
    + "unset. Clearing a setting here hands it back to each member."));

  // -- permissions + capabilities: the routine page's own two-layer control -----------------
  const permHost = el("div", { class: "mt" });
  const buildPerms = (layers) => permissionsPanel(layers.permissions, layers.capabilities, {
    onSave: async (payload) => {
      const next = cfg();
      next.permissions = payload.active || [];
      next.capabilities = payload.capabilities || {};
      if (!next.permissions.length) delete next.permissions;
      if (!Object.keys(next.capabilities).length) delete next.capabilities;
      await save(next);
      toast("group permissions saved — members inherit them at their next run");
    },
  }).node;
  permHost.append(buildPerms(g.config_layers || { permissions: [], capabilities: {} }));
  host.append(block("Permissions & capabilities",
    "held by every member on top of its own", permHost));

  // -- general rules ------------------------------------------------------------------------
  // rulePicker saves a DELTA ({add, remove}) against the set it was given, not a whole list —
  // so apply it to the group's current rules rather than storing the payload.
  const rulesHost = el("div", { class: "mt" });
  api("/api/library").then((lib) => {
    rulesHost.replaceChildren(rulePicker(lib.rules || [], g.config?.rules || [], {
      onSave: async ({ add = [], remove = [] }) => {
        const held = (g.config?.rules || []).filter((s) => !remove.includes(s));
        await put("rules", [...held, ...add.filter((s) => !held.includes(s))]);
        toast("group rules saved");
      },
    }).node);
  }).catch(() => rulesHost.replaceChildren(
    el("div", { class: "muted small" }, "could not load the rule library")));
  host.append(block("General rules", "practised by every member", rulesHost));

  // -- secret grants: the same allow/deny rows the routine page shows ------------------------
  const secHost = el("div", { class: "mt" });
  api("/api/settings/secrets").then((sec) => {
    const names = sec.keys || [];
    if (!names.length) {
      secHost.replaceChildren(el("div", { class: "muted small" }, "no secrets in the store yet"));
      return;
    }
    const rows = el("div", {});
    names.forEach((name) => {
      const id = `secret:${name}`;
      const on = (g.config?.grants || {})[id] === true;
      const box = el("input", { type: "checkbox", "data-group-secret": name,
        ...(on ? { checked: "" } : {}) });
      box.onchange = async () => {
        const grants = { ...(g.config?.grants || {}) };
        if (box.checked) grants[id] = true; else delete grants[id];
        await put("grants", grants);
        toast(box.checked ? `${name} granted to every member` : `${name} no longer granted here`);
      };
      rows.append(el("label", { class: "small", style: "display:flex;gap:6px;align-items:center" },
        box, el("code", {}, name)));
    });
    secHost.replaceChildren(rows);
  }).catch(() => secHost.replaceChildren(
    el("div", { class: "muted small" }, "could not load the secrets store")));
  host.append(block("Secrets",
    "granted to every member's util calls — including members added later", secHost));

  // -- OAuth connections --------------------------------------------------------------------
  host.append(block("Connections", "bound for every member",
    connectionsCard(g.config?.connections || {}, {
      onSave: (connections) => put("connections", connections),
    })));

  // -- filesystem read/write roots ----------------------------------------------------------
  ["fs_read_roots", "fs_write_roots"].forEach((key) => {
    const label = key === "fs_read_roots" ? "readable" : "writable";
    const ed = rootsEditor(g.config?.[key] || [],
      { pickTitle: `Directories every member may ${label === "readable" ? "read" : "write"}` });
    const btn = el("button", { class: "btn small", [`data-group-${key}-save`]: "" }, "save");
    btn.onclick = async () => { await put(key, ed.value()); toast(`group ${label} roots saved`); };
    host.append(block(`Filesystem — ${label}`,
      `added to every member's own ${label} roots`, ed.node, el("div", { class: "row mt" }, btn)));
  });

  return host;
}
