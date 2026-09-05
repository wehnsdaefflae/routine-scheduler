// "Start from a template" — a one-shot COPY, not a layer.
//
// A settings template is a preselection (operator decision 2026-08-30, reversing 0.262.0's
// layering): adopting one writes its permissions, rules and capabilities into this routine's own
// routine.yaml and then the link is gone. So this panel is an ACTION, not a view of a second
// inheritance chain — it says what applying would ADD, applies it, and afterwards every value it
// wrote is an ordinary entry in the panel that owns it, editable and removable there.
//
// That is the whole point of the reversal. Under the layer this panel had to explain what was
// inherited, what was set here, and what had been subtracted with `template_except:` — three
// concepts stacked on the routine's DOMAIN inheritance, for a starting point nobody wanted to
// keep tracking. The domain block is the one layer that stayed (D82); this one is a copy.

import { api } from "/static/api.js";
import { el, toast } from "/static/util.js";

// What a template can carry, in the words the panels below use for the same things.
const SUPPLIES = [
  { key: "permissions", label: "conduct docs" },
  { key: "rules", label: "general rules" },
  { key: "capabilities.actions", label: "gated actions" },
  { key: "capabilities.utils", label: "reserved utils" },
  { key: "capabilities.util_tags", label: "util classes" },
  { key: "machines", label: "machines" },
  { key: "tags", label: "tags" },
  { key: "fs_write_roots", label: "writable roots" },
  { key: "fs_read_roots", label: "readable roots" },
];

const dig = (obj, key) => key.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);

/** What this routine already runs with, for one key — so the preview shows only what is NEW. */
function effective(d, key) {
  if (key === "permissions") return (d.permissions || []).filter((p) => p.active).map((p) => p.slug);
  if (key === "rules") return d.rules || [];
  if (key.startsWith("capabilities.")) {
    return ((d.capabilities || {}).active || {})[key.split(".")[1]] || [];
  }
  return d[key] || [];
}

export function templatePanel(host, slug, d) {
  let templates = [];
  let detail = d;

  const sel = el("select", { "data-tpl-select": "" });
  const applyBtn = el("button", { class: "btn primary" }, "apply to this routine");
  const preview = el("div", { class: "mt", "data-tpl-preview": "" });

  const newCount = (t) => SUPPLIES.reduce((n, { key }) => {
    const give = dig(t.config || {}, key);
    if (!Array.isArray(give)) return n;
    const have = new Set(effective(detail, key));
    return n + give.filter((x) => !have.has(x)).length;
  }, 0);

  function paint() {
    const t = templates.find((x) => x.slug === sel.value) || null;
    applyBtn.disabled = !t;
    if (!t) {
      preview.replaceChildren(el("div", { class: "muted small" },
        "Pick one to see what it would add. Nothing is applied until you press the button, "
        + "and what it writes becomes this routine's own — there is no link to keep track of."));
      return;
    }
    const rows = [];
    for (const { key, label } of SUPPLIES) {
      const give = dig(t.config || {}, key);
      if (!Array.isArray(give) || !give.length) continue;
      const have = new Set(effective(detail, key));
      rows.push(el("div", { class: "tpl-row" },
        el("div", { class: "tpl-row-label" }, label),
        el("div", { class: "tpl-row-body" }, ...give.map((n) =>
          el("span", { class: `tpl-chip${have.has(n) ? " own" : ""}`,
                       ...(have.has(n) ? {} : { "data-tpl-adds": n }),
                       title: have.has(n) ? "this routine already has it"
                                          : "this would be added" },
             el("span", { class: "tpl-chip-name" }, n))))));
    }
    const added = newCount(t);
    preview.replaceChildren(...[
      el("div", { class: "prose" }, t.summary),
      el("div", { class: "muted small mt" },
        added ? `${added} entr${added === 1 ? "y" : "ies"} would be added`
              : "nothing to add — this routine already has everything it supplies",
        " · greyed entries are already here · ",
        el("a", { href: `#/library?doc=templates/${t.slug}` }, "read it")),
      ...rows,
    ].filter(Boolean));
  }

  applyBtn.onclick = async () => {
    applyBtn.disabled = true;
    try {
      const r = await api(`/api/routines/${slug}/adopt-template`,
        { method: "POST", body: { template: sel.value } });
      detail = await api(`/api/routines/${slug}`);
      paint();
      toast(r.added?.length
        ? `applied ${sel.value}: ${r.added.join(", ")} — the panels below now show them as `
          + "this routine's own"
        : r.note || "nothing to add", 5000);
    } catch (err) { toast(err.message, 4000, { error: true }); }
    finally { applyBtn.disabled = false; }
  };

  (async () => {
    let lib;
    try { lib = await api("/api/library"); } catch { return; }
    templates = lib.templates || [];
    sel.replaceChildren(
      el("option", { value: "" }, "— pick a template —"),
      ...templates.map((t) => el("option", { value: t.slug }, `${t.slug} — ${t.summary}`)));
    sel.onchange = paint;
    host.replaceChildren(el("div", { class: "row" }, sel, applyBtn), preview);
    paint();
  })();
}
