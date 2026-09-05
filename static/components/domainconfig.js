// The DOMAIN's shared routine config (D82) — the block every member inherits, edited once here
// instead of N times across the members. A domain is the SHARED-SURFACE axis
// (docs/lanes-domains.md): what a set of related routines has in common — its config block, its
// shared store, and therefore the trust boundary a domain note cannot cross. A LANE decides
// only when and in what order routines fire; it is edited on the Routines page with no config
// surface at all.
//
// Membership is NOT edited here and cannot be: a routine names its domain in its OWN
// routine.yaml, so joining or leaving one is an ordinary routine config save on that routine's
// page. That is what makes "at most one domain" a fact of the file rather than a rule someone
// has to enforce across a list — and it is why this panel only READS `members` back.
//
// It deliberately reuses the ROUTINE page's own controls — abilitiesPanel, rulePicker,
// rootsEditor, connectionsCard — so a domain's permissions look and behave exactly like a
// routine's. A lookalike built here would drift from them the first time either changes.
//
// The domain is a DEFAULT, not an override: lists union with the member's own, mappings merge
// per key with the member's value winning, and a key absent here is left entirely to each
// member (config/domainconfig.py `apply_shared_config`). Every control PATCHes the WHOLE config
// block, because the API replaces it wholesale — dropping a key returns that setting to the
// members.

import { api } from "/static/api.js";
import { connectionsCard } from "/static/components/connections.js";
import { rootsEditor } from "/static/components/fsroots.js";
import { abilitiesPanel } from "/static/components/abilities.js";
import { rulePicker } from "/static/components/rulepicker.js";
import { el, toast } from "/static/util.js";

/** One labelled block: a heading, a one-line why, and the control. */
function block(title, hint, ...nodes) {
  return el("div", { class: "mt", "data-dcfg-block": title },
    el("div", { class: "small", style: "font-weight:600" }, title),
    ...(hint ? [el("div", { class: "muted small" }, hint)] : []),
    ...nodes);
}

/** The shared-config editor for one domain.
 *  `domain` is the /api/domains record ({id, name, config, members, layers,
 *  orphan_capabilities, store}); every save PATCHes it and `onSaved` gets the fresh record. */
export function domainConfigPanel(domain, { onSaved } = {}) {
  let rec = domain;
  const host = el("div", { class: "mt", "data-domain-config": rec.id });
  const warnBox = el("div", {});
  const permHost = el("div", { class: "mt" });
  const rulesHost = el("div", { class: "mt" });
  const secHost = el("div", { class: "mt" });
  let library = null;                      // the rule library, fetched once

  // The server's answer REPLACES the record this panel was opened with, so a second edit in the
  // same sitting builds on what was actually stored — and on the freshly recomputed layers and
  // orphan list — rather than on a page-load snapshot. Throws on failure: connectionsCard
  // brings its own toast/catch; `put` below is the toasting wrapper for everything else.
  const writeConfig = async (next) => {
    rec = await api(`/api/domains/${rec.id}`, { method: "PATCH", body: { config: next } });
    renderWarnings();
    renderPerms();
    renderRules();
    onSaved?.(rec);
  };
  // Write one key of the shared block. An empty list/map is DROPPED rather than stored, so
  // "no domain value" and "an explicitly empty domain value" cannot diverge.
  const writeKey = (key, value) => {
    const next = { ...(rec.config || {}) };
    if (value == null || (Array.isArray(value) ? !value.length : !Object.keys(value).length)) {
      delete next[key];
    } else {
      next[key] = value;
    }
    return writeConfig(next);
  };
  const put = async (key, value, note) => {
    try { await writeKey(key, value); toast(note); }
    catch (err) { toast(err.message, 4000, { error: true }); }
  };

  // ORPHAN capabilities: switched on by this shared block with nothing in the SAME block
  // requiring them. Legal — a member may hold the covering conduct doc itself — but nearly
  // always a mistake and invisible everywhere downstream, so it is said at the one moment
  // someone can act on it. The server recomputes it on every read and every save.
  function renderWarnings() {
    const warns = rec.orphan_capabilities || [];
    warnBox.replaceChildren(...(warns.length
      ? [el("div", { class: "panel warn", "data-domain-warnings": "" },
          el("div", { class: "small" },
            el("b", {}, "⚠ switched on, but nothing here asks for it")),
          el("ul", { class: "small", style: "margin:6px 0 0;padding-left:18px" },
            warns.map((w) => el("li", {}, w))),
          el("div", { class: "muted small", style: "margin-top:6px" },
            "each reaches only the members that hold a covering conduct doc themselves — the "
            + "rest get the means with no conduct behind it"))]
      : []));
  }

  // -- permissions + capabilities: the routine page's own two-layer control -----------------
  function renderPerms() {
    const layers = rec.layers || { permissions: [], capabilities: {} };
    permHost.replaceChildren(abilitiesPanel(layers.permissions, layers.capabilities, {
      onSave: async (payload) => {
        const next = { ...(rec.config || {}) };
        next.permissions = payload.active || [];
        next.capabilities = payload.capabilities || {};
        if (!next.permissions.length) delete next.permissions;
        if (!Object.keys(next.capabilities).length) delete next.capabilities;
        try {
          await writeConfig(next);
          toast("domain permissions saved — members inherit them at their next run");
        } catch (err) { toast(err.message, 4000, { error: true }); }
      },
    }).node);
  }

  // -- general rules ------------------------------------------------------------------------
  // rulePicker saves a DELTA ({add, remove}) against the set it was given, not a whole list —
  // so apply it to the domain's current rules rather than storing the payload.
  function renderRules() {
    if (library === null) return;          // still loading; the fetch below renders when it lands
    rulesHost.replaceChildren(rulePicker(library, rec.config?.rules || [], {
      onSave: async ({ add = [], remove = [] }) => {
        const held = (rec.config?.rules || []).filter((s) => !remove.includes(s));
        const next = [...held, ...add.filter((s) => !held.includes(s))];
        await put("rules", next, "domain rules saved");
      },
    }).node);
  }

  const members = rec.members || [];
  // Spread a filtered array: `append` STRINGIFIES a bare null argument and renders the literal
  // word on the page, where `el()` would have dropped it. The two read identically at the call
  // site, which is why tests/test_static_dom.py scans for exactly this shape.
  host.append(...[warnBox,
    el("div", { class: "muted small" },
      "Every member inherits this. A member's own routine.yaml still wins wherever it sets the "
      + "same key — lists add together and a value here fills in only what the member leaves "
      + "unset. Clearing a setting here hands it back to each member."),
    el("div", { class: "muted small mt", "data-domain-members": "" },
      members.length
        ? ["members · ", members.join(" · "), " — each names this domain in its own config, so "
           + "joining and leaving happen on the routine's page"]
        : ["no routines name this domain yet — a routine joins from its own page"]),
    rec.store
      ? el("div", { class: "muted small mt" },
          "shared store · ", el("code", {}, rec.store),
          " — a readable AND writable root for every member's run, the boundary a note "
          + "between members cannot leave")
      : null].filter(Boolean));

  renderWarnings();
  renderPerms();
  host.append(block("Permissions & capabilities",
    "held by every member on top of its own", permHost));

  api("/api/library").then((lib) => { library = lib.rules || []; renderRules(); })
    .catch(() => rulesHost.replaceChildren(
      el("div", { class: "muted small" }, "could not load the rule library")));
  host.append(block("General rules", "practised by every member", rulesHost));

  // -- secret grants: the same allow/deny rows the routine page shows ------------------------
  api("/api/settings/secrets").then((sec) => {
    const names = sec.keys || [];
    if (!names.length) {
      secHost.replaceChildren(el("div", { class: "muted small" }, "no secrets in the store yet"));
      return;
    }
    const rows = el("div", {});
    names.forEach((name) => {
      const id = `secret:${name}`;
      const on = (rec.config?.grants || {})[id] === true;
      const box = el("input", { type: "checkbox", "data-domain-secret": name,
        ...(on ? { checked: "" } : {}) });
      box.onchange = async () => {
        const grants = { ...(rec.config?.grants || {}) };
        if (box.checked) grants[id] = true; else delete grants[id];
        await put("grants", grants,
          box.checked ? `${name} granted to every member` : `${name} no longer granted here`);
      };
      rows.append(el("label", { class: "small", style: "display:flex;gap:6px;align-items:center" },
        box, el("code", {}, name)));
    });
    secHost.replaceChildren(rows);
  }).catch(() => secHost.replaceChildren(
    el("div", { class: "muted small" }, "could not load the secrets store")));
  host.append(block("Secrets",
    "granted to every member's util calls — including members that join later", secHost));

  // -- OAuth connections --------------------------------------------------------------------
  host.append(block("Connections", "bound for every member",
    connectionsCard(rec.config?.connections || {}, {
      onSave: (connections) => writeKey("connections", connections),
    })));

  // -- filesystem read/write roots ----------------------------------------------------------
  // Built once, from the record the panel opened with: a save of one list must not throw away
  // rows typed into the other one; neither editor can be stale — nothing but these two
  // buttons writes these keys.
  ["fs_read_roots", "fs_write_roots"].forEach((key) => {
    const label = key === "fs_read_roots" ? "readable" : "writable";
    const ed = rootsEditor(rec.config?.[key] || [],
      { pickTitle: `Directories every member may ${label === "readable" ? "read" : "write"}` });
    const btn = el("button", { class: "btn small", [`data-domain-${key}-save`]: "" }, "save");
    btn.onclick = () => put(key, ed.value(), `domain ${label} roots saved`);
    host.append(block(`Filesystem — ${label}`,
      `added to every member's own ${label} roots`, ed.node, el("div", { class: "row mt" }, btn)));
  });

  return host;
}
