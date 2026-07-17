// Settings → LLM endpoints + the model catalog. Endpoints are transports (how to reach a
// provider: kind, base_url, auth); MODELS are named entries bound to an endpoint carrying the
// per-model attributes (multimodal, context window, effort, temperature). Routines/conversations
// and the system model reference a model by NAME. Also a live test call whose FULL outcome is
// surfaced — latency, schema verdict, parsed answer, and the raw error detail (auth hint) on fail.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, toast } from "/static/util.js";

// Each kind needs a DIFFERENT credential — spelled out per endpoint so the subscription token
// and metered API keys don't get confused (they land in different places).
const KIND = {
  openai: { title: "OpenAI-compatible API (OpenRouter, Featherless, vLLM, Ollama, …)", keyLabel: "API key",
    subscription: false, hint: "Needs an API key — paste it below, or set its key_var in Secrets. Setup guide: Help → endpoints." },
  anthropic: { title: "Anthropic Messages API — ⚠ METERED, per-token billing", keyLabel: "Anthropic API key (sk-ant-…)",
    subscription: false, hint: "Needs an sk-ant-… API key. This is NOT your Claude subscription." },
  "claude-cli": { title: "Claude subscription — no per-token billing", keyLabel: "subscription token",
    subscription: true, hint: "Uses your Claude Max/Pro subscription. Paste the token from `claude setup-token`." },
};
const KINDS = ["openai", "anthropic", "claude-cli"];
const SCHEMA_MODES = ["json_schema", "json_object", "ollama_native", "none"];
const EFFORTS = ["", "low", "medium", "high", "xhigh", "max"];   // "" = inherit / provider default
const NATIVE_MM = ["anthropic", "claude-cli"];   // mirrors config.NATIVE_MM_KINDS

export async function renderEndpoints(view) {
  view.append(el("div", { class: "muted small", style: "margin-bottom:8px" },
    "Model transports only — the scheduler is the only harness. None are configured by default; ",
    "add the ones you use. Each kind needs a different credential (shown per endpoint)."));
  const listBox = el("div", {});
  view.append(listBox);

  async function load() {
    const [data, secrets] = await Promise.all([
      api("/api/settings/endpoints"),
      api("/api/settings/secrets").catch(() => ({ keys: [] })),
    ]);
    listBox.replaceChildren();
    if (!data.endpoints.length)
      listBox.append(el("div", { class: "muted small" }, "no endpoints yet — add one below."));
    for (const ep of data.endpoints) listBox.append(item(ep, secrets.keys || []));
    listBox.append(addForm());
    listBox.append(modelsSection(data.endpoints, data.models || []));
    listBox.append(systemModelEditor(data.models || [], data.system_model));
  }

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
    for (const m of models) box.append(modelItem(m, endpoints));
    box.append(addModelForm(endpoints));
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
      context_chars: f.ctxIn.value.trim() ? Number(f.ctxIn.value) : null,
      effort: f.effSel.value || null,
      temperature: f.tempIn.value.trim() ? Number(f.tempIn.value) : null,
      max_tokens: f.mtIn.value.trim() ? Number(f.mtIn.value) : null,
      fallbacks: f.fbIn.value.split(",").map((s) => s.trim()).filter(Boolean),
    };
  }

  function modelFields(m, endpoints) {
    const epSel = el("select", {}, endpoints.map((e) => el("option", {}, e.name)));
    if (m.endpoint) epSel.value = m.endpoint;
    const modelIn = el("input", { type: "text", value: m.model || "", placeholder: "model id (e.g. openai/gpt-4o)", style: "width:220px" });
    const mmSel = mmSelect(m.multimodal);
    const ctxIn = el("input", { type: "number", value: m.context_chars ?? "",
      placeholder: `inherit (${(m.context_effective || 0).toLocaleString()})` });
    const effSel = el("select", {}, EFFORTS.map((e) => el("option", { value: e }, e || "default")));
    effSel.value = m.effort || "";
    const tempIn = el("input", { type: "number", step: "0.1", value: m.temperature ?? "", placeholder: "inherit" });
    const mtIn = el("input", { type: "number", value: m.max_tokens ?? "",
      placeholder: `inherit (${(m.max_tokens_effective || 0).toLocaleString()})` });
    const fbIn = el("input", { type: "text", value: (m.fallbacks || []).join(", "),
      placeholder: "fallback model names, comma-separated", style: "width:220px" });
    return { epSel, modelIn, mmSel, ctxIn, effSel, tempIn, mtIn, fbIn };
  }

  function modelFieldRows(f) {
    return el("div", {},
      el("div", { class: "field-row mt" },
        el("label", { class: "field" }, el("span", {}, "endpoint"), f.epSel),
        el("label", { class: "field" }, el("span", {}, "model id"), f.modelIn)),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "multimodal"), f.mmSel),
        el("label", { class: "field" }, el("span", {}, "context_chars"), f.ctxIn),
        el("label", { class: "field" }, el("span", {}, "effort"), f.effSel),
        el("label", { class: "field" }, el("span", {}, "temperature"), f.tempIn)),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "max_tokens (output)"), f.mtIn),
        el("label", { class: "field" }, el("span", {}, "fallbacks (failover order)"), f.fbIn)));
  }

  function modelItem(m, endpoints) {
    const f = modelFields(m, endpoints);
    const saveBtn = el("button", { class: "btn small primary" }, "save changes");
    saveBtn.onclick = async () => {
      if (!f.modelIn.value.trim()) { toast("enter a model id"); return; }
      try {
        await api(`/api/settings/models/${encodeURIComponent(m.name)}`, { method: "PUT",
          body: modelBody(m.name, f) });
        toast(`${m.name}: updated`); await load();
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    const delBtn = el("button", { class: "btn small danger" }, "delete");
    delBtn.onclick = async () => {
      if (!(await confirmDialog(`Delete model "${m.name}"?`, { confirmLabel: "delete" }))) return;
      try { await api(`/api/settings/models/${encodeURIComponent(m.name)}`, { method: "DELETE" }); await load(); }
      catch (err) { toast(err.message, 4000, { error: true }); }
    };
    return el("div", { class: "panel mt", style: "background:var(--bg)" },
      el("div", { class: "row spread" },
        el("div", {}, el("strong", {}, m.name), " ",
          el("span", { class: "muted small" }, `${m.endpoint} / ${m.model}`), " ",
          m.multimodal_effective ? el("span", { class: "chip bare", title: "sees images/PDFs natively" }, "👁") : "",
          (m.fallbacks || []).length
            ? el("span", { class: "muted small", title: "failover order on hard provider errors" },
                ` ⇢ ${m.fallbacks.join(" ⇢ ")}`) : "",
          m.max_tokens_warning
            ? el("span", { class: "chip partial", style: "margin-left:6px",
                title: m.max_tokens_warning }, "⚠ max_tokens") : ""),
        delBtn),
      el("details", { class: "mt" },
        el("summary", { style: "cursor:pointer;font-size:12px" }, "edit fields"),
        modelFieldRows(f),
        el("div", { class: "row mt" }, saveBtn)));
  }

  function addModelForm(endpoints) {
    const nameIn = el("input", { type: "text", placeholder: "name (e.g. gpt-4o)" });
    const f = modelFields({ context_effective: 0 }, endpoints);
    const save = el("button", { class: "btn primary" }, "add model");
    save.onclick = async () => {
      if (!nameIn.value.trim()) { toast("name it"); return; }
      if (!f.modelIn.value.trim()) { toast("enter a model id"); return; }
      try {
        await api("/api/settings/models", { method: "POST",
          body: modelBody(nameIn.value.trim(), f) });
        toast(`model ${nameIn.value.trim()} added`); await load();
      } catch (err) { toast(err.message, 4000, { error: true }); }
    };
    return el("details", { class: "panel mt" },
      el("summary", { style: "cursor:pointer;font-weight:600" }, "+ add model"),
      el("div", { class: "field-row mt" },
        el("label", { class: "field" }, el("span", {}, "name"), nameIn)),
      modelFieldRows(f),
      el("div", { class: "muted small", style: "margin-top:4px" },
        "multimodal = default lets the endpoint kind decide (on for anthropic/claude-cli, off for openai). ",
        "Blank context/temperature/max_tokens inherit the endpoint's. max_tokens is the model's ",
        "real OUTPUT limit — unset models ride a generic 16,384 and get flagged. ",
        "fallbacks = catalog model names tried in order when this model's provider fails hard."),
      el("div", { class: "row mt" }, save));
  }

  // The ONE fallback model for machine work that isn't a routine yet (the new-routine clarify
  // wizard + workflow generation). Setting it is what makes the instance "llm_ready". Pick a
  // catalog model by NAME; each routine then picks its own roles on its own page.
  function systemModelEditor(models, systemModel) {
    const box = el("div", { class: "panel mt" });
    box.append(
      el("div", { class: "small", style: "font-weight:600" }, "System model"),
      el("div", { class: "muted small", style: "margin:2px 0 6px" },
        "The one fallback model for setup-time work that isn't a routine yet — the new-routine ",
        "clarify wizard and workflow generation. Required before you can create routines. ",
        "Each routine then picks its own ", el("strong", {}, "main"), " / ",
        el("strong", {}, "subroutine"), " / ", el("strong", {}, "tool-call"), " models on its page."));
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
        toast(`system model → ${sel.value}`); await load();
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    box.append(el("div", { class: "row", style: "margin:5px 0" },
      el("span", { class: "ref-tag", style: "min-width:100px;text-align:center" }, "system"), sel, save));
    return box;
  }

  // The live test call: show EVERYTHING the API reports, not a bare ok/violated —
  // latency, schema verdict, parsed answer, and the raw transport error with an auth hint.
  function testResult(r) {
    if (!r.ok) {
      return el("div", { class: "test-result bad" },
        `✗ call failed${r.auth ? " (looks like an auth problem — check the key/token)" : ""}\n`,
        el("span", { class: "dim" }, r.error || "no detail"));
    }
    const verdict = r.schema_ok
      ? `✓ ok — ${r.latency_ms}ms · schema respected · answer=${r.answer}`
      : `✗ replied in ${r.latency_ms}ms but VIOLATED the schema — `
        + "the model returned unparseable output; try another schema_mode or a stronger model";
    const node = el("div", { class: `test-result ${r.schema_ok ? "ok" : "bad"}` }, verdict);
    if (r.usage && (r.usage.in || r.usage.out))
      node.append(el("span", { class: "dim" }, `  ·  ${r.usage.in || 0} in / ${r.usage.out || 0} out tok`));
    return node;
  }

  // Which rung of the credential ladder is live RIGHT NOW (inline → secret → env file),
  // labels only — and a loud warning when an inline key shadows a set secret, the exact
  // confusion where editing the secret changes nothing.
  function credSourceLine(ep) {
    const ks = ep.key_source;
    if (!ks) return "";
    const line = el("div", { class: "small", style: "margin-top:4px" }, "credential in use: ");
    if (ks.source === "inline") {
      line.append(el("span", {}, "inline key (saved on this endpoint)"));
      if (ks.shadowed_secret)
        line.append(el("span", { style: "color:var(--warn)" },
          ` — ⚠ shadows secret ${ks.var}: the inline key wins; delete it to use the secret`));
    } else if (ks.source === "secret") {
      line.append(el("span", { style: "color:var(--ok)" }, `secret ${ks.var} ✓`),
        el("span", { class: "muted" }, " (Settings → Secrets)"));
    } else if (ks.source === "env_file") {
      line.append(el("span", {}, `env file ${ks.env_file} (${ks.var})`));
    } else if (ks.source === "process_env") {
      line.append(el("span", {}, `process environment ${ks.var}`));
    } else if (ks.keyless_ok) {
      line.append(el("span", { class: "muted" },
        `none — fine for keyless local backends (Ollama, vLLM); otherwise set ${ks.var || "a key"} in Secrets`));
    } else {
      line.append(el("span", { style: "color:var(--err)" },
        `✗ missing — paste one below${ks.var ? ` or set ${ks.var} in Secrets` : ""}`));
    }
    return line;
  }

  function item(ep, secretKeys) {
    const info = KIND[ep.kind] || { title: ep.kind, keyLabel: "key", subscription: false, hint: "" };
    const modelInput = el("input", { type: "text", placeholder: "model id (e.g. opus)", style: "width:220px" });
    const resultBox = el("div", {});
    const testBtn = el("button", { class: "btn small" }, "test");
    testBtn.onclick = async () => {
      if (!modelInput.value.trim()) { toast("enter a model id to test"); return; }
      testBtn.disabled = true;
      resultBox.replaceChildren(el("div", { class: "test-result" }, "calling the model…"));
      try {
        const r = await api(`/api/settings/endpoints/${ep.name}/test`, { method: "POST", body: { model: modelInput.value.trim() } });
        resultBox.replaceChildren(testResult(r));
      } catch (err) {
        resultBox.replaceChildren(el("div", { class: "test-result bad" }, `✗ ${err.message}`));
      }
      testBtn.disabled = false;
    };
    const delBtn = el("button", { class: "btn small danger" }, "delete");
    delBtn.onclick = async () => {
      if (!(await confirmDialog(`Delete endpoint "${ep.name}"?`, { confirmLabel: "delete" }))) return;
      try { await api(`/api/settings/endpoints/${ep.name}`, { method: "DELETE" }); await load(); }
      catch (err) { toast(err.message, 4000, { error: true }); }
    };

    // credential row: subscription token → Secrets (CLAUDE_CODE_OAUTH_TOKEN); else API key → endpoint
    let keyRow;
    if (info.subscription) {
      const hasTok = secretKeys.includes("CLAUDE_CODE_OAUTH_TOKEN");
      const tokIn = el("input", { type: "password", style: "flex:1",
        placeholder: hasTok ? "token set ✓ — paste to replace" : "paste token from `claude setup-token`" });
      const saveTok = el("button", { class: "btn small primary" }, "save subscription token");
      saveTok.onclick = async () => {
        if (!tokIn.value.trim()) { toast("paste the token first"); return; }
        try { await api("/api/settings/secrets", { method: "PUT", body: { key: "CLAUDE_CODE_OAUTH_TOKEN", value: tokIn.value.trim() } });
          toast("subscription token saved (Secrets → CLAUDE_CODE_OAUTH_TOKEN)"); tokIn.value = ""; await load(); }
        catch (err) { toast(err.message, 5000, { error: true }); }
      };
      keyRow = el("div", {}, el("div", { class: "row mt" }, tokIn, saveTok),
        el("div", { class: "faint small" }, "stored in Secrets — feeds this endpoint and `gu claude`"));
    } else {
      const keyInput = el("input", { type: "password", style: "flex:1",
        placeholder: ep.has_inline_key ? `${info.keyLabel} set ✓ — paste to replace` : `paste ${info.keyLabel} (or set ${ep.key_var || "its key_var"} in Secrets)` });
      const saveKey = el("button", { class: "btn small primary" }, "save key");
      saveKey.onclick = async () => {
        if (!keyInput.value.trim()) { toast("paste a key first"); return; }
        try {
          await api(`/api/settings/endpoints/${ep.name}`, { method: "PUT", body: {
            name: ep.name, kind: ep.kind, base_url: ep.base_url || "", key_env_file: ep.key_env_file || "",
            key_var: ep.key_var || "", schema_mode: ep.schema_mode, context_chars: ep.context_chars, api_key: keyInput.value.trim() } });
          toast(`${ep.name}: key saved`); keyInput.value = ""; await load();
        } catch (err) { toast(err.message, 5000, { error: true }); }
      };
      keyRow = el("div", { class: "row mt" }, keyInput, saveKey);
    }

    // editable fields (name is the identity, immutable). context_chars/temperature are DEFAULTS
    // catalog models inherit when they leave the field unset.
    const kindSel = el("select", {}, KINDS.map((k) => el("option", {}, k))); kindSel.value = ep.kind;
    const schemaSel = el("select", {}, SCHEMA_MODES.map((m) => el("option", {}, m))); schemaSel.value = ep.schema_mode || "json_schema";
    const baseIn = el("input", { type: "text", value: ep.base_url || "", placeholder: "https://host/v1" });
    const keyVarIn = el("input", { type: "text", value: ep.key_var || "", placeholder: "KEY_VAR in Secrets (optional)" });
    const ctxIn = el("input", { type: "number", value: ep.context_chars });
    const mtIn = el("input", { type: "number", value: ep.max_tokens ?? "", placeholder: "inherit (16,384)" });
    const saveEdit = el("button", { class: "btn small primary" }, "save changes");
    saveEdit.onclick = async () => {
      try {
        await api(`/api/settings/endpoints/${ep.name}`, { method: "PUT", body: {
          name: ep.name, kind: kindSel.value, base_url: baseIn.value.trim(), key_env_file: ep.key_env_file || "",
          key_var: keyVarIn.value.trim(), schema_mode: schemaSel.value, context_chars: Number(ctxIn.value) || 100000,
          max_tokens: mtIn.value.trim() ? Number(mtIn.value) : null } });
        toast(`${ep.name}: updated`); await load();
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    const editForm = el("details", { class: "mt" },
      el("summary", { style: "cursor:pointer;font-size:12px" }, "edit fields"),
      el("div", { class: "field-row mt" },
        el("label", { class: "field" }, el("span", {}, "kind"), kindSel),
        el("label", { class: "field" }, el("span", {}, "base_url"), baseIn)),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "key_var (Secrets)"), keyVarIn),
        el("label", { class: "field" }, el("span", {}, "schema_mode"), schemaSel),
        el("label", { class: "field" }, el("span", {}, "context_chars (default)"), ctxIn),
        el("label", { class: "field" }, el("span", {}, "max_tokens (default)"), mtIn)),
      el("div", { class: "row" }, saveEdit));

    // Account balance, for providers that expose one (OpenRouter, Nano-GPT) — lazy per card.
    const creditsRow = el("div", { class: "small muted", style: "margin-top:4px" });
    if (["openrouter", "nano-gpt.com"].some((p) => (ep.base_url || "").includes(p))) {
      creditsRow.textContent = "credits: checking…";
      api(`/api/settings/endpoints/${encodeURIComponent(ep.name)}/credits`).then((c) => {
        if (!c.supported) { creditsRow.replaceChildren(); return; }
        const detail = c.total != null
          ? ` (used $${c.used.toFixed(2)} of $${c.total.toFixed(2)})` : "";
        creditsRow.replaceChildren(
          c.ok
            ? el("span", { style: "color:var(--ok)" }, `$${c.remaining.toFixed(2)} remaining${detail}`)
            : el("span", {}, `credits unavailable — ${c.error}`),
          ...(c.manage_url ? [" · ", el("a", { href: c.manage_url, target: "_blank",
                                               rel: "noopener" }, "manage credits ↗")] : []));
      }).catch(() => creditsRow.replaceChildren());
    }

    return el("div", { class: "panel mt" },
      el("div", { class: "row spread" },
        el("div", {}, el("strong", {}, ep.name), " ", el("span", { class: "chip bare" }, ep.kind), " ",
          el("span", { class: "muted small" }, ep.base_url || "")),
        delBtn),
      el("div", { class: "small" }, info.title),
      el("div", { class: "muted small", style: "margin-bottom:2px" }, info.hint),
      credSourceLine(ep),
      creditsRow,
      keyRow,
      el("div", { class: "row mt" }, modelInput, testBtn),
      resultBox,
      editForm);
  }

  function addForm() {
    const nameIn = el("input", { type: "text", placeholder: "name (e.g. openrouter)" });
    const kindSel = el("select", {}, KINDS.map((k) => el("option", {}, k)));
    const baseIn = el("input", { type: "text", placeholder: "https://host/v1" });
    const keyVarIn = el("input", { type: "text", placeholder: "KEY_VAR in Secrets (optional)" });
    const schemaSel = el("select", {}, SCHEMA_MODES.map((m) => el("option", {}, m)));
    const ctxIn = el("input", { type: "number", value: "200000" });
    const hint = el("div", { class: "muted small" });
    const onKind = () => {
      const k = KIND[kindSel.value]; hint.textContent = k ? `${k.title} — ${k.hint}` : "";
    };
    kindSel.onchange = onKind; onKind();
    const save = el("button", { class: "btn primary" }, "add endpoint");
    save.onclick = async () => {
      if (!nameIn.value.trim()) { toast("name it"); return; }
      try {
        await api("/api/settings/endpoints", { method: "POST", body: {
          name: nameIn.value.trim(), kind: kindSel.value, base_url: baseIn.value.trim(),
          key_var: keyVarIn.value.trim(), schema_mode: schemaSel.value,
          context_chars: Number(ctxIn.value) || 200000 } });
        toast(`endpoint ${nameIn.value.trim()} added — set its ${KIND[kindSel.value]?.keyLabel || "key"} on its card, then add a model`); await load();
      } catch (err) { toast(err.message, 4000, { error: true }); }
    };
    return el("details", { class: "panel mt" },
      el("summary", { style: "cursor:pointer;font-weight:600" }, "+ add endpoint"),
      el("div", { class: "field-row mt" },
        el("label", { class: "field" }, el("span", {}, "name"), nameIn),
        el("label", { class: "field" }, el("span", {}, "kind"), kindSel)),
      hint,
      el("div", { class: "field-row mt" },
        el("label", { class: "field" }, el("span", {}, "base_url"), baseIn),
        el("label", { class: "field" }, el("span", {}, "key_var (Secrets)"), keyVarIn),
        el("label", { class: "field" }, el("span", {}, "schema_mode"), schemaSel),
        el("label", { class: "field" }, el("span", {}, "context_chars (default)"), ctxIn)),
      el("div", { class: "row" }, save));
  }

  await load();
}
