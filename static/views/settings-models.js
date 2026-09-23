// Settings → the MODEL CATALOG: named models bound to an endpoint, plus the two instance-wide
// model choices that read from it — the system model (the scheduler's own helper calls) and the
// context-compaction model.
//
// Split out of settings-endpoints.js, which held three independent surfaces in one 654-line
// closure: the transports, this catalog, and the proxy sign-in flow. An endpoint is HOW to
// reach a provider; a model is a named entry with its own attributes — multimodality, context
// window, effort, temperature — and routines reference the model, never the endpoint. Two
// concepts, two modules.
//
// `reload` is the endpoints view's own load(): every save here re-reads the whole settings
// payload, because a model change moves the system-model and compaction pickers too.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { temperatureHint } from "/static/views/settings-common.js";
import { el, toast, toastError } from "/static/util.js";

const EFFORTS = ["", "low", "medium", "high", "xhigh", "max"];   // "" = inherit / provider default

/** The three catalog panels, in the order the page shows them. */
export function modelPanels({ endpoints, models, systemModel, compactionModel, reload }) {
  // ---- the model catalog: named models bound to an endpoint -----------------------------------
  function modelsSection(endpoints, models) {
    const box = el("div", { class: "panel mt" });
    box.append(
      el("div", { class: "small", style: "font-weight:600" }, "Models"),
      el("div", { class: "muted small", style: "margin:2px 0 6px" },
        "Named models bound to an endpoint. Each carries its OWN multimodality, context window, ",
        "effort and temperature — one endpoint serves many models. Routines and conversations ",
        "pick a model by name."));
    if (!endpoints.length) {
      box.append(el("div", { class: "muted small" }, "add an endpoint above first"));
      return box;
    }
    if (!models.length)
      box.append(el("div", { class: "muted small" }, "no models yet — add one below."));
    for (const m of models) box.append(modelItem(m, endpoints, models));
    box.append(addModelForm(endpoints, models));
    return box;
  }

  // multimodal is tri-state: default (by endpoint kind) | on | off. Stored null/true/false.
  function mmSelect(cur) {
    const sel = el("select", {}, ["default", "on", "off"].map((o) => el("option", {}, o)));
    sel.value = cur === true ? "on" : cur === false ? "off" : "default";
    return sel;
  }
  const mmValue = (sel) => (sel.value === "on" ? true : sel.value === "off" ? false : null);

  function modelBody(name, f) {
    return {
      name, endpoint: f.epSel.value, model: f.modelIn.value.trim(),
      multimodal: mmValue(f.mmSel),
      context_tokens: f.ctxIn.value.trim() ? Number(f.ctxIn.value) : null,
      effort: f.effSel.value || null,
      temperature: f.tempIn.value.trim() ? Number(f.tempIn.value) : null,
      max_tokens: f.mtIn.value.trim() ? Number(f.mtIn.value) : null,
      fallbacks: f.fbIn.value(),
    };
  }

  //: Where a model's effective window came from, in one word for the placeholder.
  const SOURCE_HINT = {
    openrouter: "read from OpenRouter's own /models listing — leave blank to track it",
    nanogpt: "read from Nano-GPT's own model listing — leave blank to track it",
    ollama: "read from this Ollama server's /api/show — leave blank to track it",
    openai: "read from the provider's /models listing — leave blank to track it",
    table: "no metadata API for this kind; a built-in table supplies the window",
    endpoint: "no provider figure for this model id — the endpoint's own default applies",
    floor: "no provider figure for this model id — the engine floor applies",
    config: "set by hand here, which overrides what the provider reports",
  };
  const sourceWord = (m) => ({ openrouter: "from openrouter", nanogpt: "from nano-gpt",
    ollama: "from ollama", openai: "from the provider", table: "from the built-in table",
    endpoint: "endpoint fallback", floor: "engine fallback", config: "model override",
  })[m.window?.window_source] || "inherit";

  // Fallbacks name OTHER rows of this very catalog, so the field offers the catalog instead of
  // accepting prose. A free-text box let "Opus 5" be typed, look accepted, and be refused only by
  // the server after a round trip (3 refused saves on 2026-09-10) — the client had the names all
  // along. Order matters (it is the failover sequence), so this is an ordered add/remove list, not
  // a set of checkboxes. A name already configured but no longer in the catalog is KEPT and marked,
  // exactly as the compaction-model picker does, so opening this card never silently drops config.
  function fallbackPicker(current, models, selfName) {
    const chosen = [...current];
    const row = el("div", { class: "row", style: "flex-wrap:wrap;gap:4px;align-items:center" });
    const candidates = () => models
      .map((x) => x.name)
      .filter((n) => n !== selfName && !chosen.includes(n));
    const render = () => {
      row.replaceChildren();
      chosen.forEach((name, i) => {
        const known = models.some((x) => x.name === name);
        const drop = el("button", { class: "btn small", title: `remove ${name}` }, "×");
        drop.onclick = () => { chosen.splice(i, 1); render(); };
        row.append(el("span", { class: known ? "ref-tag" : "chip partial",
          title: known ? "failover step" : `${name} is not in the catalog — it will be refused on save` },
          `${i + 1}. ${name}`, drop));
      });
      const left = candidates();
      if (left.length) {
        const add = el("select", { "aria-label": "add a fallback model" },
          el("option", { value: "" }, chosen.length ? "+ then try…" : "+ add a fallback"),
          ...left.map((n) => el("option", { value: n }, n)));
        add.onchange = () => { if (add.value) { chosen.push(add.value); render(); } };
        row.append(add);
      } else if (!chosen.length) {
        row.append(el("span", { class: "muted small" }, "no other catalog model to fall back to"));
      }
    };
    render();
    return { node: row, value: () => [...chosen] };
  }

  function modelFields(m, endpoints, models = []) {
    const epSel = el("select", {}, endpoints.map((e) => el("option", {}, e.name)));
    if (m.endpoint) epSel.value = m.endpoint;
    const modelIn = el("input", { type: "text", value: m.model || "", placeholder: "model id (e.g. openai/gpt-4o)", style: "width:220px" });
    const mmSel = mmSelect(m.multimodal);
    // The placeholder now says where the effective figure CAME FROM. Leaving these blank is the
    // correct, normal state since limits are discovered — an empty box that said only "inherit"
    // read as "unset, go and guess a number", which is exactly how 16 of 17 models ended up on
    // one endpoint-wide guess.
    const ctxIn = el("input", { type: "number", value: m.context_tokens ?? "",
      title: SOURCE_HINT[m.window?.window_source] || "",
      placeholder: `${sourceWord(m)} (${(m.context_effective || 0).toLocaleString()})` });
    const effSel = el("select", {}, EFFORTS.map((e) => el("option", { value: e }, e || "default")));
    effSel.value = m.effort || "";
    const tempIn = el("input", { type: "number", step: "0.1", value: m.temperature ?? "", placeholder: "inherit" });
    const mtIn = el("input", { type: "number", value: m.max_tokens ?? "",
      placeholder: `inherit (${(m.max_tokens_effective || 0).toLocaleString()})` });
    const fbIn = fallbackPicker(m.fallbacks || [], models, m.name);
    return { epSel, modelIn, mmSel, ctxIn, effSel, tempIn, mtIn, fbIn };
  }

  function modelFieldRows(f) {
    return el("div", {},
      el("div", { class: "field-row mt" },
        el("label", { class: "field" }, el("span", {}, "endpoint"), f.epSel),
        el("label", { class: "field" }, el("span", {}, "model id"), f.modelIn)),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "multimodal"), f.mmSel),
        el("label", { class: "field" }, el("span", {}, "Context window (tokens)"), f.ctxIn),
        el("label", { class: "field" }, el("span", {}, "effort"), f.effSel),
        el("label", { class: "field" }, el("span", {}, "temperature"), f.tempIn,
          temperatureHint())),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "max_tokens (output)"), f.mtIn),
        el("label", { class: "field" }, el("span", {}, "fallbacks (failover order)"), f.fbIn.node)));
  }

  function modelItem(m, endpoints, models) {
    const f = modelFields(m, endpoints, models);
    const saveBtn = el("button", { class: "btn small primary" }, "save changes");
    saveBtn.onclick = async () => {
      if (!f.modelIn.value.trim()) { toast("enter a model id"); return; }
      try {
        await api(`/api/settings/models/${encodeURIComponent(m.name)}`, { method: "PUT",
          body: modelBody(m.name, f) });
        toast(`${m.name}: updated`); await reload();
      } catch (err) { toastError(err, 5000); }
    };
    const delBtn = el("button", { class: "btn small danger" }, "delete");
    delBtn.onclick = async () => {
      if (!(await confirmDialog(`Delete model "${m.name}"?`, { confirmLabel: "delete" }))) return;
      try { await api(`/api/settings/models/${encodeURIComponent(m.name)}`, { method: "DELETE" }); await reload(); }
      catch (err) { toastError(err); }
    };
    return el("div", { class: "panel mt", style: "background:var(--deck-2)" },
      el("div", { class: "row spread" },
        el("div", {}, el("strong", {}, m.name), " ",
          el("span", { class: "muted small" }, `${m.endpoint} / ${m.model}`), " ",
          m.multimodal_effective ? el("span", { class: "chip bare", title: "sees images/PDFs natively" }, "👁") : "",
          (m.fallbacks || []).length
            ? el("span", { class: "muted small", title: "failover order on hard provider errors" },
                ` ⇢ ${m.fallbacks.join(" ⇢ ")}`) : "",
          m.max_tokens_warning
            ? el("span", { class: "chip partial", style: "margin-left:6px",
                title: m.max_tokens_warning }, "⚠ max_tokens") : "",
          m.window_warning
            ? el("span", { class: "chip partial", style: "margin-left:6px",
                title: m.window_warning }, "⚠ window") : "",
          el("span", { class: "muted small", style: "margin-left:8px",
              title: SOURCE_HINT[m.window?.window_source] || "" },
            `${(m.window?.context_tokens || 0).toLocaleString()} tok · ${sourceWord(m)}`)),
        delBtn),
      el("details", { class: "mt" },
        el("summary", { style: "cursor:pointer;font-size:12px" }, "edit fields"),
        modelFieldRows(f),
        el("div", { class: "row mt" }, saveBtn)));
  }

  function addModelForm(endpoints, models) {
    const nameIn = el("input", { type: "text", placeholder: "name (e.g. gpt-4o)" });
    const f = modelFields({ context_effective: 0 }, endpoints, models);
    const save = el("button", { class: "btn primary" }, "add model");
    save.onclick = async () => {
      if (!nameIn.value.trim()) { toast("name it"); return; }
      if (!f.modelIn.value.trim()) { toast("enter a model id"); return; }
      try {
        await api("/api/settings/models", { method: "POST",
          body: modelBody(nameIn.value.trim(), f) });
        toast(`model ${nameIn.value.trim()} added`); await reload();
      } catch (err) { toastError(err); }
    };
    return el("details", { class: "panel mt" },
      el("summary", { style: "cursor:pointer;font-weight:600" }, "+ add model"),
      el("div", { class: "field-row mt" },
        el("label", { class: "field" }, el("span", {}, "name"), nameIn)),
      modelFieldRows(f),
      el("div", { class: "muted small", style: "margin-top:4px" },
        "multimodal = default lets the endpoint kind decide (on for anthropic, off for openai). ",
        "Blank context and output limits use provider metadata, then endpoint defaults. ",
        "The context window includes input and output; max_tokens reserves output tokens. ",
        "fallbacks = other catalog models, tried in the order shown when this model's provider fails hard."),
      el("div", { class: "row mt" }, save));
  }

  // The ONE fallback model for machine work that isn't a routine yet (the new-routine clarify
  // creation flow + workflow generation). Setting it is what makes the instance "llm_ready". Pick a
  // catalog model by NAME; each routine then picks its own roles on its own page.
  function systemModelEditor(models, systemModel) {
    const box = el("div", { class: "panel mt" });
    box.append(
      el("div", { class: "small", style: "font-weight:600" }, "System model"),
      el("div", { class: "muted small", style: "margin:2px 0 6px" },
        "The one fallback model for setup-time work that isn't a routine yet — the new-routine ",
        "clarify flow and workflow generation. Required before you can create routines. ",
        "Each routine then picks its own ", el("strong", {}, "main"), " / ",
        el("strong", {}, "tool-call"), " models on its page (children run main by default) — ",
        "any role a routine leaves unset falls back to this system model. (A model may also name its ",
        "own per-model ", el("strong", {}, "fallbacks"), " above — a separate hard-failure failover chain.)"));
    if (!models.length) {
      box.append(el("div", { class: "muted small" }, "add a model above first"));
      return box;
    }
    const sel = el("select", {}, models.map((m) => el("option", {}, m.name)));
    if (systemModel) sel.value = systemModel;
    const save = el("button", { class: "btn small primary" }, systemModel ? "update" : "set");
    save.onclick = async () => {
      try {
        await api("/api/settings/system-model", { method: "PUT", body: { name: sel.value } });
        toast(`system model → ${sel.value}`); await reload();
      } catch (err) { toastError(err, 5000); }
    };
    box.append(el("div", { class: "row", style: "margin:5px 0" },
      el("span", { class: "ref-tag", style: "min-width:100px;text-align:center" }, "system"), sel, save));
    return box;
  }

  function compactionModelEditor(models, current) {
    const sel = el("select", { "aria-label": "Context compaction model" },
      el("option", { value: "" }, "Automatic"),
      ...models.map((m) => el("option", { value: m.name }, m.name)));
    if (current && !models.some((m) => m.name === current))
      sel.append(el("option", { value: current }, `${current} (unavailable)`));
    sel.value = current || "";
    const save = el("button", { class: "btn small primary" }, "save compaction model");
    save.onclick = async () => {
      try {
        await api("/api/settings/compaction-model", { method: "PUT", body: { name: sel.value } });
        toast(`compaction model → ${sel.value || "Automatic"}`); await reload();
      } catch (err) { toastError(err, 5000); }
    };
    return el("div", { class: "panel mt" },
      el("div", { class: "small", style: "font-weight:600" }, "Context compaction model"),
      el("p", { class: "muted small" },
        "Builds navigable history in the background; does not change the foreground context window, main model, or observation compression. ",
        "Automatic uses tool-call when its window fits, otherwise the current main model. ",
        "An unavailable or too-small dedicated model falls back with a recorded reason. ",
        "If archival fails, the deterministic digest and full transcript remain."),
      el("div", { class: "row" }, sel, save));
  }

  return [modelsSection(endpoints, models),
          systemModelEditor(models, systemModel),
          compactionModelEditor(models, compactionModel)];
}
