// The SETTINGS TEMPLATE panel of the routine page: which named starting point this routine
// adopts, what that template actually supplies it, what it drops from it, and what is set on
// this routine alone.
//
// The picker on its own was not enough. A template layers UNDER the routine's own config
// (rsched/templates.py), so every panel further down the page shows the EFFECTIVE value with
// no sign of which layer produced it — and a subtraction (`template_except:`) is invisible by
// construction, because a dropped entry is simply an entry that is not there. This panel is
// the one place both are legible — and the only place `template_except` is editable at all.
//
// The split is exact rather than heuristic: the template's own `config` says what it supplies,
// so anything ACTIVE on the routine that the template does not supply was set here (or by the
// routine's group, which the inherited-note above names). No guessing from counts.

import { api } from "/static/api.js";
import { el, toast } from "/static/util.js";

// The five lists `template_except:` can subtract from — config/routine.py filters exactly
// these, so exactly these get a drop control. Everything else a template carries (budgets,
// roots, models, grants…) is shown as a read-only tally: it is layered, not subtractable.
const DROPPABLE = [
  { key: "permissions", label: "conduct docs" },
  { key: "rules", label: "general rules" },
  { key: "capabilities.actions", label: "gated actions" },
  { key: "capabilities.utils", label: "reserved utils" },
  { key: "capabilities.util_tags", label: "util classes" },
];

// Keys a template may carry that are layered whole rather than entry-by-entry.
const TALLIED = [
  { key: "machines", label: "machines" },
  { key: "tags", label: "tags" },
  { key: "fs_write_roots", label: "writable roots" },
  { key: "fs_read_roots", label: "readable roots" },
  { key: "models", label: "model roles" },
  { key: "connections", label: "connections" },
  { key: "grants", label: "secret grants" },
  { key: "budgets", label: "budgets" },
];

const dig = (obj, key) => key.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);

/** What the template supplies for one droppable key. */
function supplied(tpl, key) {
  const v = tpl ? dig(tpl.config || {}, key) : null;
  return Array.isArray(v) ? v : [];
}

/** What this routine actually RUNS with for one droppable key — the effective config. */
function effective(d, key) {
  if (key === "permissions") return (d.permissions || []).filter((p) => p.active).map((p) => p.slug);
  if (key === "rules") return d.rules || [];
  const caps = (d.capabilities || {}).active || {};
  return caps[key.split(".")[1]] || [];
}

function count(v) {
  if (Array.isArray(v)) return v.length;
  if (v && typeof v === "object") return Object.keys(v).length;
  return v ? 1 : 0;
}

export function templatePanel(host, slug, d) {
  let templates = [];
  let detail = d;

  const sel = el("select", { "data-tpl-select": "" });
  const summary = el("div", { class: "muted small mt" });
  const layers = el("div", { class: "mt", "data-tpl-layers": "" });

  // A drop / restore writes the whole `template_except` list back — it is a small set the
  // user is looking at, so a read-modify-write of the visible state is honest and needs no
  // merge rules. The routine detail is re-read afterwards so the panel repaints from the
  // server's answer rather than from what we hoped it did.
  async function setExcept(next) {
    try {
      await api(`/api/routines/${slug}`, { method: "PATCH", body: { template_except: next } });
      detail = await api(`/api/routines/${slug}`);
      paint();
      toast("template exceptions saved — they apply from the next run");
    } catch (err) { toast(err.message, 4000, { error: true }); }
  }

  function entryChip(name, key, dropped) {
    const btn = el("button", {
      class: "btn small",
      title: dropped ? "put it back — the template supplies it again"
                     : "drop it — this routine runs without what the template supplies here",
      onclick: () => {
        const cur = detail.template_except || [];
        setExcept(dropped ? cur.filter((x) => x !== name) : [...cur, name]);
      },
    }, dropped ? "↺" : "✕");
    return el("span", {
      class: `tpl-chip${dropped ? " dropped" : ""}`,
      "data-tpl-supplies": name, "data-tpl-key": key,
      ...(dropped ? { "data-dropped": "" } : {}),
    }, el("span", { class: "tpl-chip-name" }, name), btn);
  }

  function row(label, ...body) {
    return el("div", { class: "tpl-row" },
      el("div", { class: "tpl-row-label" }, label),
      el("div", { class: "tpl-row-body" }, ...body));
  }

  function paint() {
    const chosen = templates.find((t) => t.slug === sel.value) || null;
    const adopted = templates.find((t) => t.slug === detail.template) || null;
    // The summary describes the SELECTED template (what saving would adopt); the layers below
    // describe the ADOPTED one (what this routine runs with today). Conflating them would
    // show a routine settings it does not have the moment the picker is touched.
    if (!chosen) {
      summary.replaceChildren("Nothing is inherited — every setting below is this routine's own.");
    } else {
      const c = chosen.config || {};
      // .filter(Boolean): replaceChildren/append STRINGIFY a null argument into the text
      // "null" — unlike el(), which drops null children. Every conditional child here goes
      // through the filter for that reason.
      summary.replaceChildren(...[
        el("div", { class: "prose" }, chosen.summary),
        el("div", { class: "mt" }, "supplies ",
          el("b", {}, `${(c.permissions || []).length} conduct docs`), ", ",
          el("b", {}, `${(c.rules || []).length} general rules`), ", ",
          el("b", {}, `${((c.capabilities || {}).actions || []).length} actions`),
          ((c.capabilities || {}).utils || []).length
            ? ` and ${c.capabilities.utils.length} reserved util(s)` : "",
          " · previous runs: ", el("code", {}, (c.capabilities || {}).runs || "none"),
          el("a", { href: `#/library?doc=templates/${chosen.slug}`, style: "margin-left:10px" },
             "read it")),
        chosen.slug !== detail.template
          ? el("div", { class: "warn small mt" },
              detail.template
                ? `not saved yet — this routine still runs on “${detail.template}”`
                : "not saved yet — this routine still runs on its own config alone")
          : null,
      ].filter(Boolean));
    }

    const dropped = new Set(detail.template_except || []);
    layers.replaceChildren();
    if (!adopted) {
      // A named-but-missing template is a real state (the library can lose one) and the
      // surface check reports it; say it here too, where the picker that caused it lives.
      if (detail.template) {
        layers.append(el("div", { class: "panel err mt" },
          `⚠ this routine names the template “${detail.template}”, which the library does not `
          + "have — it runs on its own config alone."));
      }
      return;
    }

    const fromTpl = [], ownRows = [];
    for (const { key, label } of DROPPABLE) {
      const give = supplied(adopted, key);
      const mine = effective(detail, key);
      if (give.length) {
        fromTpl.push(row(label, ...give.map((n) => entryChip(n, key, dropped.has(n)))));
      }
      // Active here and NOT supplied by the template — so it was set on this routine (or by
      // its group, which the inherited note above the panels names).
      const own = mine.filter((n) => !give.includes(n));
      if (own.length) {
        ownRows.push(row(label, ...own.map((n) =>
          el("span", { class: "tpl-chip own", "data-tpl-own": n, "data-tpl-key": key },
             el("span", { class: "tpl-chip-name" }, n)))));
      }
    }
    const tally = TALLIED.map(({ key, label }) => [label, count(dig(adopted.config || {}, key))])
      .filter(([, n]) => n > 0).map(([label, n]) => `${label} (${n})`);
    // A drop for something this template does not supply is dead weight, not an error — it
    // survives a template switch. Naming it is the only way a user can clear it.
    const stale = [...dropped].filter((n) =>
      !DROPPABLE.some(({ key }) => supplied(adopted, key).includes(n)));

    layers.append(...[
      el("h3", { class: "tpl-head" }, `Inherited from “${adopted.slug}”`),
      fromTpl.length ? el("div", {}, ...fromTpl)
        : el("div", { class: "muted small" },
            "this template supplies nothing that can be dropped entry by entry"),
      tally.length ? el("div", { class: "muted small mt" }, "also layered: ", tally.join(" · "))
        : null,
      stale.length ? row("dropped, but not supplied by this template",
        ...stale.map((n) => entryChip(n, "", true))) : null,
      el("h3", { class: "tpl-head" }, "Set on this routine"),
      ownRows.length ? el("div", {}, ...ownRows)
        : el("div", { class: "muted small" },
            "nothing beyond the template — this routine is the template"),
    ].filter(Boolean));
  }

  (async () => {
    let lib;
    try { lib = await api("/api/library"); } catch { return; }
    templates = lib.templates || [];
    sel.replaceChildren(
      el("option", { value: "" }, "— none (set everything on this routine) —"),
      ...templates.map((t) => el("option", { value: t.slug }, `${t.slug} — ${t.summary}`)));
    sel.value = detail.template || "";
    sel.onchange = paint;
    host.replaceChildren(
      el("div", { class: "row" }, sel,
        el("button", { class: "btn primary", onclick: async () => {
          try {
            await api(`/api/routines/${slug}`, { method: "PATCH", body: { template: sel.value } });
            detail = await api(`/api/routines/${slug}`);
            paint();
            toast(sel.value ? `template: ${sel.value} — applies from the next run`
                            : "template cleared");
          } catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save template")),
      summary, layers);
    paint();
  })();
}
