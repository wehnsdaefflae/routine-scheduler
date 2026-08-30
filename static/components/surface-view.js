// The EFFECTIVE SURFACE, read-only: every dependency this routine's setup resolves to, the
// satisfied ones included, grouped by what put each one there.
//
// `setupcheck.js` shows the same join filtered to what is UNMET — deliberately, because a strip
// that is always present is a strip nobody reads. But that leaves the other half unreadable:
// "the secrets this routine can actually reach", "the roots its utils actually need", "which
// conduct doc is the reason it holds this at all" are questions the panels below cannot answer,
// because each panel shows one layer and the answer is the join of all of them.
//
// So this is the same endpoint, unfiltered, ordered worst-first, with each row hung under its
// PROVENANCE — `node.source` is machine-readable for exactly this (`{doc}` / `{utils}`), which
// is why grouping here never has to parse the prose in `why`.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

const LABEL = { blocks: "fails", interrupts: "interrupts", note: "note", ok: "ok" };

function sourceKey(node) {
  const src = node.source || {};
  if (src.doc) return `doc:${src.doc}`;
  if (src.utils?.length) return `util:${src.utils.join(", ")}`;
  return "";
}

function sourceLabel(key) {
  if (key.startsWith("doc:")) return [`from the conduct doc `, el("code", {}, key.slice(4))];
  if (key.startsWith("util:")) return [`declared by `, el("code", {}, key.slice(5))];
  return ["from this routine's own config"];
}

function row(node) {
  return el("tr", { class: `sev-${node.severity}`, "data-surface-row": node.id },
    el("td", {}, el("span", { class: "setup-sev" }, LABEL[node.severity] || node.severity)),
    el("td", {}, el("code", {}, node.id)),
    el("td", { class: "muted" }, node.state || ""),
    el("td", { class: "muted prose" }, node.why || "",
      node.effect ? el("div", { class: "faint small" }, node.effect) : null));
}

/** Renders into `host`. Read-only on purpose: every row is FIXED in the panel that owns it
 *  (a root in Filesystem roots, a secret in Secret exposure) — a second place to edit the
 *  same value is a second place for it to be wrong. */
export function surfaceView(host, slug, surface = null) {
  const body = el("div", { "data-surface-view": "" });
  host.append(body);

  function paint(data) {
    const nodes = data.nodes || [];
    if (!nodes.length) {
      body.replaceChildren(el("div", { class: "faint small" },
        "nothing resolves yet — this routine holds no conduct doc, rule or reserved util that "
        + "declares a dependency."));
      return;
    }
    const v = data.verdict || {};
    const groups = new Map();
    for (const n of nodes) {
      const key = sourceKey(n);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(n);
    }
    body.replaceChildren(el("div", { class: "muted small" },
      `${nodes.length} resolved dependenc${nodes.length === 1 ? "y" : "ies"} · `,
      el("b", {}, v.blocks ? `${v.blocks} will fail` : "nothing will fail"),
      v.interrupts ? ` · ${v.interrupts} will interrupt` : "",
      v.notes ? ` · ${v.notes} note${v.notes > 1 ? "s" : ""}` : "",
      el("div", { class: "faint" },
        "recomputed on every read — the library moves under a routine, so a stored answer "
        + "would be stale the first time somebody ran write_util.")));
    // Own-config rows last: an operator scanning for "why does it hold this?" is looking for
    // a doc or a util; the rows with no source are the ones they already know about.
    for (const [key, rows] of [...groups].sort((a, b) => (a[0] ? 0 : 1) - (b[0] ? 0 : 1))) {
      body.append(
        el("div", { class: "tpl-head" }, ...sourceLabel(key)),
        el("div", { class: "tablewrap" },
          el("table", { class: "list surface-table" }, el("tbody", {}, ...rows.map(row)))));
    }
  }

  if (surface) { paint(surface); return { refresh: () => {} }; }
  const refresh = async () => {
    try { paint(await api(`/api/routines/${slug}/surface`)); }
    catch { body.replaceChildren(el("div", { class: "faint small" }, "surface unavailable")); }
  };
  refresh();
  return { refresh };
}
