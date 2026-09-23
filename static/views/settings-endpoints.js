// Settings → LLM endpoints + the model catalog. Endpoints are transports (how to reach a
// provider: kind, base_url, auth); MODELS are named entries bound to an endpoint carrying the
// per-model attributes (multimodal, context window, effort, temperature). Routines/conversations
// and the system model reference a model by NAME. Also a live test call whose FULL outcome is
// surfaced — latency, schema verdict, parsed answer, and the raw error detail (auth hint) on fail.

import { api } from "/static/api.js";
import { quotaLine } from "/static/components/quota.js";
import { confirmDialog } from "/static/components/dialog.js";
import { proxyAccounts } from "/static/components/proxy-accounts.js";
import { modelPanels } from "/static/views/settings-models.js";
import { temperatureHint } from "/static/views/settings-common.js";
import { el, toast, toastError } from "/static/util.js";

// Each kind needs a DIFFERENT credential — spelled out per endpoint so the subscription token
// and metered API keys don't get confused (they land in different places).
const KIND = {
  openai: { title: "OpenAI-compatible API (OpenRouter, Featherless, vLLM, Ollama, …)", keyLabel: "API key",
    subscription: false, hint: "Needs an API key — paste it below, or set its key_var in Secrets. Setup guide: Help → endpoints." },
  anthropic: { title: "Anthropic-compatible Messages API", keyLabel: "API or proxy client key",
    subscription: false, hint: "Direct Anthropic uses a metered API key. A subscription proxy uses its own client key; sign in through the proxy." },
};
const KINDS = ["openai", "anthropic"];
const SCHEMA_MODES = ["json_schema", "json_object", "ollama_native", "none"];

export async function renderEndpoints(view) {
  view.append(el("div", { class: "set-desc muted small" },
    "Model transports only — the scheduler is the only harness. None are configured by default; ",
    "add the ones you use. Each kind needs a different credential (shown per endpoint)."));
  const listBox = el("div", {});
  view.append(listBox);

  async function load() {
    const data = await api("/api/settings/endpoints");
    listBox.replaceChildren();
    if (!data.endpoints.length)
      listBox.append(el("div", { class: "muted small" }, "no endpoints yet — add one below."));
    for (const ep of data.endpoints) listBox.append(item(ep));
    listBox.append(addForm());
    listBox.append(...modelPanels({
      endpoints: data.endpoints, models: data.models || [], systemModel: data.system_model,
      compactionModel: data.compaction_model, reload: load }));
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

  function item(ep) {
    const info = KIND[ep.kind] || { title: ep.kind, keyLabel: "key", subscription: false, hint: "" };
    const modelInput = el("input", { type: "text", placeholder: "model id (e.g. opus)", style: "width:220px" });
    const resultBox = el("div", {});
    const testBtn = el("button", { class: "btn small" }, "test");
    testBtn.onclick = async () => {
      if (!modelInput.value.trim()) { toast("enter a model id to test"); return; }
      testBtn.disabled = true;
      resultBox.replaceChildren(el("div", { class: "test-result" }, "calling the model…"));
      try {
        const r = await api(`/api/settings/endpoints/${encodeURIComponent(ep.name)}/test`, { method: "POST", body: { model: modelInput.value.trim() } });
        resultBox.replaceChildren(testResult(r));
      } catch (err) {
        resultBox.replaceChildren(el("div", { class: "test-result bad" }, `✗ ${err.message}`));
      }
      testBtn.disabled = false;
    };
    const delBtn = el("button", { class: "btn small danger" }, "delete");
    delBtn.onclick = async () => {
      if (!(await confirmDialog(`Delete endpoint "${ep.name}"?`, { confirmLabel: "delete" }))) return;
      try { await api(`/api/settings/endpoints/${encodeURIComponent(ep.name)}`, { method: "DELETE" }); await load(); }
      catch (err) { toastError(err); }
    };

    // Proxy client keys and provider API keys use the same secret store.
    let keyRow;
    {
      const keyInput = el("input", { type: "password", style: "flex:1",
        placeholder: ep.has_inline_key ? `${info.keyLabel} set ✓ — paste to replace` : `paste ${info.keyLabel} (or set ${ep.key_var || "its key_var"} in Secrets)` });
      const saveKey = el("button", { class: "btn small primary" }, "save key");
      saveKey.onclick = async () => {
        if (!keyInput.value.trim()) { toast("paste a key first"); return; }
        try {
          await api(`/api/settings/endpoints/${encodeURIComponent(ep.name)}`, { method: "PUT", body: {
            name: ep.name, kind: ep.kind, base_url: ep.base_url || "", key_env_file: ep.key_env_file || "",
            key_var: ep.key_var || "", schema_mode: ep.schema_mode, context_tokens: ep.context_tokens, api_key: keyInput.value.trim() } });
          toast(`${ep.name}: key saved`); keyInput.value = ""; await load();
        } catch (err) { toastError(err, 5000); }
      };
      keyRow = el("div", { class: "row mt" }, keyInput, saveKey);
    }

    // editable fields (name is the identity, immutable). context_tokens/temperature are DEFAULTS
    // catalog models inherit when they leave the field unset.
    const kindSel = el("select", {}, KINDS.map((k) => el("option", {}, k))); kindSel.value = ep.kind;
    const schemaSel = el("select", {}, SCHEMA_MODES.map((m) => el("option", {}, m))); schemaSel.value = ep.schema_mode || "json_schema";
    const baseIn = el("input", { type: "text", value: ep.base_url || "", placeholder: "https://host/v1" });
    const keyVarIn = el("input", { type: "text", value: ep.key_var || "", placeholder: "KEY_VAR in Secrets (optional)" });
    const keyEnvIn = el("input", { type: "text", value: ep.key_env_file || "", placeholder: "path to an env file (optional)" });
    const quotaSel = el("select", {}, el("option", { value: "" }, "None"),
      el("option", { value: "cliproxy" }, "CLIProxyAPI")); quotaSel.value = ep.quota_source || "";
    const quotaKeyIn = el("input", { type: "text", value: ep.quota_key_var || "CLIPROXY_MANAGEMENT_KEY" });
    const quotaAccountIn = el("input", { type: "text", value: ep.quota_auth_index || "",
      placeholder: "Automatic for one Claude account" });
    const ctxIn = el("input", { type: "number", value: ep.context_tokens });
    const tempIn = el("input", { type: "number", step: "0.1", value: ep.temperature ?? "", placeholder: "provider default" });
    const mtIn = el("input", { type: "number", value: ep.max_tokens ?? "", placeholder: "inherit (16,384)" });
    const extraBodyIn = ep.kind === "openai"
      ? el("textarea", { class: "code", rows: "3", style: "width:100%",
          placeholder: '{"provider": {"ignore": ["…"]}}' },
          ep.extra_body ? JSON.stringify(ep.extra_body, null, 2) : "") : null;
    const saveEdit = el("button", { class: "btn small primary" }, "save changes");
    saveEdit.onclick = async () => {
      const body = {
        name: ep.name, kind: kindSel.value, base_url: baseIn.value.trim(),
        quota_source: quotaSel.value, quota_key_var: quotaKeyIn.value.trim(),
        quota_auth_index: quotaAccountIn.value.trim(),
        key_env_file: keyEnvIn.value.trim(), key_var: keyVarIn.value.trim(),
        schema_mode: schemaSel.value, context_tokens: Number(ctxIn.value) || 25000,
        temperature: tempIn.value.trim() ? Number(tempIn.value) : null,
        max_tokens: mtIn.value.trim() ? Number(mtIn.value) : null };
      if (extraBodyIn) {
        const raw = extraBodyIn.value.trim();
        try { body.extra_body = raw ? JSON.parse(raw) : {}; }
        catch { toast("extra_body must be valid JSON", 4000, { error: true }); return; }
      }
      try {
        await api(`/api/settings/endpoints/${encodeURIComponent(ep.name)}`, { method: "PUT", body });
        toast(`${ep.name}: updated`); await load();
      } catch (err) { toastError(err, 5000); }
    };
    const editForm = el("details", { class: "mt" },
      el("summary", { style: "cursor:pointer;font-size:12px" }, "edit fields"),
      el("div", { class: "field-row mt" },
        el("label", { class: "field" }, el("span", {}, "kind"), kindSel),
        el("label", { class: "field" }, el("span", {}, "base_url"), baseIn)),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "key_var (Secrets)"), keyVarIn),
        el("label", { class: "field" }, el("span", {}, "key_env_file"), keyEnvIn),
        el("label", { class: "field" }, el("span", {}, "schema_mode"), schemaSel)),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "Context window (tokens, fallback)"), ctxIn),
        el("label", { class: "field" }, el("span", {}, "temperature (default)"), tempIn,
          temperatureHint()),
        el("label", { class: "field" }, el("span", {}, "max_tokens (default)"), mtIn)),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "Proxy management (CLIProxyAPI)"), quotaSel),
        el("label", { class: "field" }, el("span", {}, "Management key secret name"), quotaKeyIn),
        el("label", { class: "field" }, el("span", {}, "Quota account index"), quotaAccountIn)),
      extraBodyIn ? el("div", { style: "margin-top:6px" },
        el("div", { class: "field" },
          el("span", {}, "extra_body (JSON — merged into every request)"), extraBodyIn)) : null,
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

    // Proxy subscription: the REAL per-window quota, read from the account's own
    // usage API. This replaced a local token tally (D33) that could not express "% remaining" in
    // principle — Anthropic's windows are not a token count, and the tally was blind to the
    // operator's own interactive sessions on the same subscription.
    const usageRow = el("div", { class: "small muted", style: "margin-top:4px" });
    function loadQuota() {
      usageRow.textContent = "subscription quota: checking…";
      api(`/api/settings/endpoints/${encodeURIComponent(ep.name)}/quota`).then((q) => {
        if (!q.supported) { usageRow.replaceChildren(); return; }
        if (!q.ok) {
          // The error is the actionable half here — it always names the one-line fix, because
          // the credential this needs expires; the proxy-account rows below carry the way back in.
          usageRow.replaceChildren(el("span", { class: "warn-line" },
            `subscription quota unavailable — ${q.error || "unknown reason"}`));
          return;
        }
        usageRow.replaceChildren(el("span", { title: "from the account's own usage API — the "
          + "whole subscription, including your interactive sessions" },
          `subscription: ${quotaLine(q)}`));
      }).catch(() => usageRow.replaceChildren());
    }
    if (ep.has_subscription_quota) loadQuota();
    // The signed-in accounts and the way back in, on every endpoint of a bound proxy (the
    // binding is the proxy's, resolved server-side) — a finished sign-in also changes what the
    // quota read returns, so it reloads that row.
    const proxy = ep.proxy_management ? proxyAccounts(ep, loadQuota) : null;

    return el("div", { class: "panel mt" },
      el("div", { class: "row spread" },
        el("div", {}, el("strong", {}, ep.name), " ", el("span", { class: "chip bare" }, ep.kind), " ",
          el("span", { class: "muted small" }, ep.base_url || "")),
        delBtn),
      el("div", { class: "small" }, info.title),
      el("div", { class: "muted small", style: "margin-bottom:2px" }, info.hint),
      credSourceLine(ep),
      creditsRow,
      usageRow,
      proxy ? proxy.box : null,
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
    const ctxIn = el("input", { type: "number", value: "25000" });
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
          context_tokens: Number(ctxIn.value) || 25000 } });
        toast(`endpoint ${nameIn.value.trim()} added — set its ${KIND[kindSel.value]?.keyLabel || "key"} on its card, then add a model`); await load();
      } catch (err) { toastError(err); }
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
        el("label", { class: "field" }, el("span", {}, "Context window (tokens, fallback)"), ctxIn)),
      el("div", { class: "row" }, save));
  }

  await load();
}
