// Playground -> the RETRIEVAL BENCH: one situation, every store, both backends side by side.
//
// This is the panel that answers the design thread's actual question. You describe a turn by
// filling in its components — what was done (A), what came back (O), what the run made of it
// (R), what it means to do next (I), the person's words (P) — pick which of them form the
// query, and see what each store returns under token overlap versus embeddings.
//
// Why the component checkboxes matter: the thread's conclusion was that the right query differs
// per helper. Catching work a run did BY HAND needs the evidential components (A+O), because a
// run re-implementing a util does not intend to; prospective selection needs the interpretive
// ones (R+I). This panel is where that prediction gets checked against your real stores rather
// than argued about.

import { api } from "/static/api.js";
import { chip, el, emptyState, toastError } from "/static/util.js";

const STORE_LABEL = {
  utils: "utils", memory: "memory notes", reminders: "reminders", rules: "rules",
};

// Which components are pre-ticked: the evidential pair, because that is the recipe the thread
// predicted would win for the failure the whole experiment is about.
const DEFAULT_COMPONENTS = new Set(["A", "O"]);

export function benchPanel(meta) {
  const box = el("div", { class: "panel" });
  box.append(el("h3", {}, "Retrieval bench"),
    el("div", { class: "muted small", style: "margin-bottom:10px" },
      "Describe one turn, choose which of its parts form the query, and compare what the ",
      "stores return. Nothing here touches a conversation — it is the same index the live ",
      "layer uses, queried by hand."));

  const inputs = {};
  for (const c of meta.components) {
    const field = c.id === "A" || c.id === "O"
      ? el("textarea", { rows: 2, placeholder: c.label })
      : el("input", { type: "text", placeholder: c.label });
    inputs[c.id] = field;
    const tick = el("input", { type: "checkbox" });
    tick.checked = DEFAULT_COMPONENTS.has(c.id);
    tick.dataset.comp = c.id;
    box.append(el("div", { class: "row", style: "align-items:flex-start;margin:6px 0" },
      el("label", { class: "row small", style: "min-width:210px;gap:6px;align-items:center" },
        tick, el("b", {}, c.id), el("span", { class: "muted" }, c.label.split("—")[0].trim())),
      field));
    field.style.flex = "1";
  }

  const storeTicks = [];
  const storeRow = el("div", { class: "row small", style: "gap:12px;margin:10px 0" },
    el("span", { class: "muted" }, "stores:"));
  for (const s of meta.stores) {
    const t = el("input", { type: "checkbox" });
    t.checked = true;
    t.dataset.store = s;
    storeTicks.push(t);
    storeRow.append(el("label", { class: "row", style: "gap:5px;align-items:center" },
      t, STORE_LABEL[s] || s));
  }
  box.append(storeRow);

  const embeddings = meta.backends.find((b) => b.name === "embeddings");
  if (embeddings && !embeddings.available) {
    box.append(el("div", { class: "faint small", style: "margin:4px 0 8px" },
      "embeddings column unavailable — ", embeddings.why,
      ". The lexical column is the baseline either way; it is what the engine runs today."));
  }

  const run = el("button", { class: "btn small primary" }, "compare");
  const out = el("div", { style: "margin-top:12px" });
  box.append(el("div", { class: "row" }, run), out);

  run.onclick = async () => {
    const body = { stores: storeTicks.filter((t) => t.checked).map((t) => t.dataset.store) };
    let any = false;
    for (const tick of box.querySelectorAll("input[data-comp]")) {
      if (!tick.checked) continue;
      const value = inputs[tick.dataset.comp].value.trim();
      if (value) { body[tick.dataset.comp] = value; any = true; }
    }
    if (!any) {
      out.replaceChildren(el("div", { class: "muted small" },
        "Tick at least one component and give it some text."));
      return;
    }
    run.disabled = true;
    out.replaceChildren(el("div", { class: "muted small" }, "querying…"));
    try {
      const res = await api("/api/playground/bench", { method: "POST", body });
      out.replaceChildren(renderResults(res, meta));
    } catch (err) {
      toastError(err);
      out.replaceChildren(el("div", { class: "muted small" }, "the query failed — see the toast"));
    } finally {
      run.disabled = false;
    }
  };

  return box;
}

function renderResults(res, meta) {
  const wrap = el("div");
  wrap.append(el("div", { class: "faint small", style: "margin-bottom:8px" },
    `query = ${res.components.join(" + ")} over ${res.indexed} indexed conditions`));

  const cols = el("div", { class: "row", style: "gap:18px;align-items:flex-start" });
  cols.append(column("lexical — token overlap (what runs today)", res.results.lexical, meta));
  cols.append(column("embeddings — cosine similarity",
    res.results.embeddings, meta, res.embeddings_why));
  wrap.append(cols);
  return wrap;
}

function column(title, hits, meta, why) {
  const box = el("div", { style: "flex:1;min-width:0" });
  box.append(el("div", { class: "small", style: "margin-bottom:6px" }, el("b", {}, title)));
  if (hits === null || hits === undefined) {
    box.append(el("div", { class: "faint small" }, why || "not available"));
    return box;
  }
  if (!hits.length) {
    box.append(el("div", { class: "muted small" }, "nothing matched"));
    return box;
  }
  for (const h of hits) {
    // The floor is drawn on every row, because a hit BELOW it is what the live layer would
    // have thrown away — reading the list without that line invites judging the layer by
    // matches it would never have surfaced.
    const below = h.score < meta.min_score;
    box.append(el("div", { class: "row small", style: "gap:8px;align-items:baseline;padding:3px 0" },
      el("span", { class: "ref-tag", style: "min-width:52px;text-align:center" },
        h.score.toFixed(2)),
      el("b", { style: below ? "opacity:.55" : "" }, h.ident),
      chip(STORE_LABEL[h.store] || h.store),
      h.owner ? chip(h.owner) : "",
      below ? el("span", { class: "faint" }, "below floor") : "",
      el("span", { class: "muted", style: "flex:1;min-width:0" },
        (h.detail || h.condition || "").slice(0, 150))));
  }
  return box;
}

export function emptyBench() {
  return emptyState("⚗", "No stores indexed",
    "The playground found no utils, notes, reminders or rules to query.");
}
