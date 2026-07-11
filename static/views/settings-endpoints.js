// Settings → LLM endpoints: list, add/edit, delete, system model, and a live test call whose
// FULL outcome is surfaced — latency, schema verdict, parsed answer, and the raw error detail
// (with an auth hint) on failure. Endpoints are model transports only.

import { api } from "/static/api.js";
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
    for (const ep of data.endpoints) listBox.append(item(ep, data.system_model, secrets.keys || []));
    listBox.append(addForm());
    listBox.append(systemModelEditor(data.endpoints, data.system_model));
  }

  // The ONE fallback model for machine work that isn't a routine yet (the new-routine clarify
  // wizard + workflow generation). Setting it is what makes the instance "llm_ready". Each
  // routine picks its own three models (main / subroutine / tool-call) on its own page.
  function systemModelEditor(endpoints, systemModel) {
    const box = el("div", { class: "panel mt" });
    box.append(
      el("div", { class: "small", style: "font-weight:600" }, "System model"),
      el("div", { class: "muted small", style: "margin:2px 0 6px" },
        "The one fallback model for setup-time work that isn't a routine yet — the new-routine ",
        "clarify wizard and workflow generation. Required before you can create routines. ",
        "Each routine then picks its own ", el("strong", {}, "main"), " / ",
        el("strong", {}, "subroutine"), " / ", el("strong", {}, "tool-call"), " models on its page."));
    if (!endpoints.length) {
      box.append(el("div", { class: "muted small" }, "add an endpoint above first"));
      return box;
    }
    const cur = systemModel || {};
    const epSel = el("select", {}, endpoints.map((e) => el("option", {}, e.name)));
    if (cur.endpoint) epSel.value = cur.endpoint;
    const modelIn = el("input", { type: "text", value: cur.model || "", placeholder: "model id (e.g. z-ai/glm-5.2)", style: "width:220px" });
    const save = el("button", { class: "btn small primary" }, cur.endpoint ? "update" : "set");
    save.onclick = async () => {
      if (!modelIn.value.trim()) { toast("enter a model id"); return; }
      try {
        await api("/api/settings/system-model", { method: "PUT", body: { endpoint: epSel.value, model: modelIn.value.trim() } });
        toast(`system model → ${epSel.value} / ${modelIn.value.trim()}`); await load();
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    box.append(el("div", { class: "row", style: "margin:5px 0" },
      el("span", { class: "ref-tag", style: "min-width:100px;text-align:center" }, "system"), epSel, modelIn, save));
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

  function item(ep, systemModel, secretKeys) {
    const info = KIND[ep.kind] || { title: ep.kind, keyLabel: "key", subscription: false, hint: "" };
    const modelGuess = (systemModel && systemModel.endpoint === ep.name) ? systemModel.model : "";
    const modelInput = el("input", { type: "text", value: modelGuess, placeholder: "model id (e.g. opus)", style: "width:220px" });
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
      if (!confirm(`Delete endpoint "${ep.name}"?`)) return;
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

    // editable fields (name is the identity, immutable)
    const kindSel = el("select", {}, KINDS.map((k) => el("option", {}, k))); kindSel.value = ep.kind;
    const schemaSel = el("select", {}, SCHEMA_MODES.map((m) => el("option", {}, m))); schemaSel.value = ep.schema_mode || "json_schema";
    const baseIn = el("input", { type: "text", value: ep.base_url || "", placeholder: "https://host/v1" });
    const keyVarIn = el("input", { type: "text", value: ep.key_var || "", placeholder: "KEY_VAR in Secrets (optional)" });
    const ctxIn = el("input", { type: "number", value: ep.context_chars });
    const saveEdit = el("button", { class: "btn small primary" }, "save changes");
    saveEdit.onclick = async () => {
      try {
        await api(`/api/settings/endpoints/${ep.name}`, { method: "PUT", body: {
          name: ep.name, kind: kindSel.value, base_url: baseIn.value.trim(), key_env_file: ep.key_env_file || "",
          key_var: keyVarIn.value.trim(), schema_mode: schemaSel.value, context_chars: Number(ctxIn.value) || 100000 } });
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
        el("label", { class: "field" }, el("span", {}, "context_chars"), ctxIn)),
      el("div", { class: "row" }, saveEdit));

    return el("div", { class: "panel mt" },
      el("div", { class: "row spread" },
        el("div", {}, el("strong", {}, ep.name), " ", el("span", { class: "chip bare" }, ep.kind), " ",
          el("span", { class: "muted small" }, ep.base_url || "")),
        delBtn),
      el("div", { class: "small" }, info.title),
      el("div", { class: "muted small", style: "margin-bottom:2px" }, info.hint),
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
    const setHint = () => { const k = KIND[kindSel.value]; hint.textContent = k ? `${k.title} — ${k.hint}` : ""; };
    kindSel.onchange = setHint; setHint();
    const save = el("button", { class: "btn primary" }, "add endpoint");
    save.onclick = async () => {
      if (!nameIn.value.trim()) { toast("name it"); return; }
      try {
        await api("/api/settings/endpoints", { method: "POST", body: {
          name: nameIn.value.trim(), kind: kindSel.value, base_url: baseIn.value.trim(),
          key_var: keyVarIn.value.trim(), schema_mode: schemaSel.value, context_chars: Number(ctxIn.value) || 200000 } });
        toast(`endpoint ${nameIn.value.trim()} added — set its ${KIND[kindSel.value]?.keyLabel || "key"} on its card`); await load();
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
        el("label", { class: "field" }, el("span", {}, "context_chars"), ctxIn)),
      el("div", { class: "row" }, save));
  }

  await load();
}
