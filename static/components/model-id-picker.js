// The model-id field: a <select> of the ids an endpoint ITSELF says it serves, a plain text
// box only when it cannot say.
//
// Typing a provider id by hand is how a catalog entry came to name something the endpoint never
// served. The server asks the endpoint (GET /api/settings/endpoints/<name>/model-ids, the
// endpoint's own key stays there) and answers `{ok, ids}` or `{ok:false, error}`; this swaps the
// text box for a dropdown only on a non-empty listing, so a failure or an empty answer costs the
// operator nothing they had before — the box stays, with the reason beside it. A value the
// catalog already holds is KEPT as an option even when the listing lacks it, marked "not
// listed": opening a card must never silently change config.
//
// Two callers: the catalog model form (the endpoint select drives `setEndpoint`) and the
// endpoint card's test box (one fixed endpoint). Answers are cached per endpoint for a short
// while — one page shows a model form per catalog entry — and only the newest request paints.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

const TTL_MS = 30_000;
const cache = new Map();   // endpoint name → { at, promise }

function listing(endpoint, force) {
  const hit = cache.get(endpoint);
  if (hit && !force && Date.now() - hit.at < TTL_MS) return hit.promise;
  const promise = api(`/api/settings/endpoints/${encodeURIComponent(endpoint)}/model-ids`)
    .catch((err) => ({ ok: false, error: err.message }));
  cache.set(endpoint, { at: Date.now(), promise });
  return promise;
}

/** `{ node, value(), setEndpoint(name, force?) }` — `node` is a block (the control, then the one-line reason or count) that goes into a `label.field`. */
export function modelIdPicker({ value = "", placeholder = "model id", width = "220px",
                                label = "model id" } = {}) {
  let current = value;
  let wanted = 0;
  const hint = el("div", { class: "muted small" });
  const box = el("div", {});

  const textBox = () => {
    const input = el("input", { type: "text", value: current, placeholder,
      "aria-label": label, style: `width:${width}` });
    input.oninput = () => { current = input.value; };
    return input;
  };
  let control = textBox();
  box.append(control, hint);

  function show(next) { control.replaceWith(next); control = next; }

  function dropdown(ids) {
    const sel = el("select", { "aria-label": label });
    if (!current) sel.append(el("option", { value: "" }, "— choose a model id —"));
    if (current && !ids.includes(current))
      sel.append(el("option", { value: current }, `${current} (not listed)`));
    for (const id of ids) sel.append(el("option", { value: id }, id));
    sel.value = current;
    sel.onchange = () => { current = sel.value; };
    return sel;
  }

  async function setEndpoint(name, force = false) {
    const mine = ++wanted;
    if (!name) return;
    hint.textContent = "asking the endpoint which models it serves…";
    const r = await listing(name, force);
    if (mine !== wanted) return;            // a newer endpoint choice owns the field
    if (r.ok && r.ids.length) {
      hint.textContent = `${r.ids.length} offered by this endpoint`;
      show(dropdown(r.ids));
      return;
    }
    hint.textContent = r.ok
      ? "this endpoint lists no models (a proxy lists none for a signed-out account) — type the id"
      : `${r.error} — type the id`;
    if (control.tagName !== "INPUT") show(textBox());
  }

  return { node: box, value: () => (control.value ?? current).trim(), setEndpoint };
}
