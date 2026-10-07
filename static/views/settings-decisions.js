// Settings → Decision endpoints: the catalog behind the `decide` action (docs/decision-models.md).
//
// A decision model (TypeSafe's Jev, OpenAI's Decisions API, the predator server) answers a typed
// question with probabilities over the answers it was offered — never text — so it has its own
// endpoints and models beside the LLM ones, and no chat role can pick one. Two wire PROTOCOLS
// cover every provider; the add form's presets fill in the protocol, base URL and key name for
// the known ones. Each model carries a live probe: one yes/no question with an obvious answer.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { savedToast } from "/static/views/settings-common.js";
import { act, el, skeleton, toast } from "/static/util.js";

const PROTOCOL = {
  openai: "OpenAI Decisions protocol — POST {base}/decisions, text and images",
  systemone: "System One protocol (Jev) — POST {base}/systemone, text only",
};
// Known providers: picking one fills the form. `key_var` names the Secrets entry the key is
// read from, so an OpenRouter key already stored for the LLM endpoint serves Jev as well.
const PRESETS = [
  { id: "openai", label: "OpenAI Decisions API (gpt-6-luna)", protocol: "openai",
    base_url: "https://api.openai.com/v1", key_var: "OPENAI_API_KEY", model: "gpt-6-luna" },
  { id: "openrouter", label: "Jev via OpenRouter (typesafe/jev-1.13)", protocol: "systemone",
    base_url: "https://openrouter.ai/api/v1", key_var: "OPENROUTER_API_KEY",
    model: "typesafe/jev-1.13" },
  { id: "typesafe", label: "Jev direct from TypeSafe (jev-latest)", protocol: "systemone",
    base_url: "https://api.typesafe.ai/v1", key_var: "TYPESAFE_API_KEY", model: "jev-latest" },
  { id: "self", label: "Self-hosted, OpenAI-shaped (e.g. deploy/decision-server)",
    protocol: "openai", base_url: "http://host:8790/v1", key_var: "", model: "" },
];

function credLine(ep) {
  const ks = ep.key_source || {};
  const line = el("div", { class: "small", style: "margin-top:4px" }, "credential in use: ");
  if (ks.source === "inline") line.append("inline key (saved on this endpoint)");
  else if (ks.source === "secret")
    line.append(el("span", { style: "color:var(--ok)" }, `secret ${ks.var} ✓`));
  else if (ks.source === "env_file") line.append(`env file ${ks.env_file} (${ks.var})`);
  else if (ks.keyless_ok)
    line.append(el("span", { class: "muted" },
      `none — fine for a self-hosted server without a token; otherwise set ${ks.var || "a key"} in Secrets`));
  else line.append(el("span", { style: "color:var(--err)" },
    `✗ missing — paste one below${ks.var ? ` or set ${ks.var} in Secrets` : ""}`));
  return line;
}

function probeResult(r) {
  if (!r.ok)
    return el("div", { class: "test-result bad" },
      `✗ call failed${r.auth ? " (looks like an auth problem — check the key)" : ""}\n`,
      el("span", { class: "dim" }, r.error || "no detail"));
  if (r.refused) return el("div", { class: "test-result bad" }, `✗ the model refused the probe (${r.latency_ms}ms)`);
  const good = r.probability >= 0.5;
  return el("div", { class: `test-result ${good ? "ok" : "bad"}` },
    `${good ? "✓" : "✗"} ${r.latency_ms}ms · P(yes) = ${Number(r.probability).toFixed(3)} on a question whose answer is yes`,
    el("span", { class: "dim" }, `  ·  ${r.served_by}  ·  ${r.usage?.in || 0} in tok`));
}

export async function renderDecisions(view) {
  view.append(el("div", { class: "set-desc muted small" },
    "Decision models answer a typed question — yes/no, a choice, a score — with probabilities ",
    "over the answers offered, never text. Routines reach them through the decide action, which ",
    "they only see once a model is configured here."));
  // A plain box like the LLM endpoints' (its cards are the panels), appended synchronously so
  // the section keeps its place; a failed read is painted here instead of rejecting the page.
  const box = el("div", {}, skeleton(["50%", "100%", "80%"]));
  view.append(box);
  async function reload() {
    let d;
    try { d = await api("/api/settings/decisions"); }
    catch (err) { box.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    box.replaceChildren(
      ...d.endpoints.map((ep) => endpointCard(ep, reload)),
      d.endpoints.length ? "" : el("div", { class: "muted small" }, "no decision endpoints yet — add one below."),
      addEndpointForm(reload),
      el("div", { class: "kicker", style: "margin-top:16px" }, "Decision models"),
      ...d.models.map((m) => modelRow(m, d, reload)),
      d.endpoints.length ? addModelForm(d, reload) : "",
      d.models.length ? defaultsEditor(d, reload) : "");
  }
  await reload();
}

function endpointCard(ep, reload) {
  const url = `/api/settings/decision-endpoints/${encodeURIComponent(ep.name)}`;
  const body = (over) => ({ name: ep.name, protocol: ep.protocol, base_url: ep.base_url || "",
    key_var: ep.key_var || "", key_env_file: ep.key_env_file || "", timeout_s: ep.timeout_s, ...over });
  const del = el("button", { class: "btn small danger" }, "delete");
  del.onclick = () => act(del, async () => {
    if (!(await confirmDialog(`Delete decision endpoint "${ep.name}"?`, { confirmLabel: "delete" }))) return;
    savedToast(await api(url, { method: "DELETE" }), `decision endpoint ${ep.name} deleted`);
    await reload();
  });
  const keyIn = el("input", { type: "password", style: "flex:1",
    placeholder: ep.has_inline_key ? "key set ✓ — paste to replace" : `paste a key (or set ${ep.key_var || "its key_var"} in Secrets)` });
  const saveKey = el("button", { class: "btn small primary" }, "save key");
  saveKey.onclick = () => act(saveKey, async () => {
    if (!keyIn.value.trim()) { toast("paste a key first"); return; }
    savedToast(await api(url, { method: "PUT", body: body({ api_key: keyIn.value.trim() }) }), `${ep.name}: key saved`);
    await reload();
  });
  const protoSel = el("select", {}, Object.keys(PROTOCOL).map((p) => el("option", {}, p)));
  protoSel.value = ep.protocol;
  const baseIn = el("input", { type: "text", value: ep.base_url || "", placeholder: ep.default_base_url });
  const keyVarIn = el("input", { type: "text", value: ep.key_var || "" });
  const timeoutIn = el("input", { type: "number", min: "5", max: "3600", value: ep.timeout_s });
  const save = el("button", { class: "btn small primary" }, "save changes");
  save.onclick = () => act(save, async () => {
    savedToast(await api(url, { method: "PUT", body: body({ protocol: protoSel.value,
      base_url: baseIn.value.trim(), key_var: keyVarIn.value.trim(),
      timeout_s: Number(timeoutIn.value) || ep.timeout_s }) }), `${ep.name}: updated`);
    await reload();
  });
  return el("div", { class: "panel mt", "data-decision-endpoint": ep.name },
    el("div", { class: "row spread" },
      el("div", {}, el("strong", {}, ep.name), " ", el("span", { class: "chip bare" }, ep.protocol), " ",
        el("span", { class: "muted small" }, ep.base_url || `${ep.default_base_url} (default)`)),
      del),
    el("div", { class: "small" }, PROTOCOL[ep.protocol] || ep.protocol),
    credLine(ep),
    el("div", { class: "row mt" }, keyIn, saveKey),
    el("details", { class: "mt" },
      el("summary", { style: "cursor:pointer;font-size:12px" }, "edit fields"),
      el("div", { class: "field-row mt" },
        el("label", { class: "field" }, el("span", {}, "protocol"), protoSel),
        el("label", { class: "field" }, el("span", {}, "base_url"), baseIn)),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "key_var (Secrets)"), keyVarIn),
        el("label", { class: "field" }, el("span", {}, "timeout (s)"), timeoutIn)),
      el("div", { class: "row" }, save)));
}

function addEndpointForm(reload) {
  const presetSel = el("select", {}, el("option", { value: "" }, "choose a provider…"),
    PRESETS.map((p) => el("option", { value: p.id }, p.label)));
  const nameIn = el("input", { type: "text", placeholder: "name (e.g. openrouter-jev)" });
  const protoSel = el("select", {}, Object.keys(PROTOCOL).map((p) => el("option", {}, p)));
  const baseIn = el("input", { type: "text", placeholder: "blank: the protocol's vendor" });
  const keyVarIn = el("input", { type: "text", placeholder: "KEY_VAR in Secrets" });
  const hint = el("div", { class: "muted small" });
  const onProto = () => { hint.textContent = PROTOCOL[protoSel.value]; };
  protoSel.onchange = onProto; onProto();
  presetSel.onchange = () => {
    const p = PRESETS.find((x) => x.id === presetSel.value);
    if (!p) return;
    nameIn.value ||= p.id; protoSel.value = p.protocol; baseIn.value = p.base_url;
    keyVarIn.value = p.key_var; onProto();
  };
  const save = el("button", { class: "btn primary" }, "add decision endpoint");
  save.onclick = () => act(save, async () => {
    if (!nameIn.value.trim()) { toast("name it"); return; }
    savedToast(await api("/api/settings/decision-endpoints", { method: "POST", body: {
      name: nameIn.value.trim(), protocol: protoSel.value, base_url: baseIn.value.trim(),
      key_var: keyVarIn.value.trim() } }), `decision endpoint ${nameIn.value.trim()} added — now add a model on it`);
    await reload();
  });
  return el("details", { class: "panel mt" },
    el("summary", { style: "cursor:pointer;font-weight:600" }, "+ add decision endpoint"),
    el("div", { class: "field-row mt" },
      el("label", { class: "field" }, el("span", {}, "provider"), presetSel),
      el("label", { class: "field" }, el("span", {}, "name"), nameIn),
      el("label", { class: "field" }, el("span", {}, "protocol"), protoSel)),
    hint,
    el("div", { class: "field-row mt" },
      el("label", { class: "field" }, el("span", {}, "base_url"), baseIn),
      el("label", { class: "field" }, el("span", {}, "key_var (Secrets)"), keyVarIn)),
    el("div", { class: "row" }, save));
}

function modelRow(m, d, reload) {
  const url = `/api/settings/decision-models/${encodeURIComponent(m.name)}`;
  const defaults = [m.name === d.decision_model ? "default" : "",
    m.name === d.decision_media_model ? "default for images" : ""].filter(Boolean);
  const result = el("div", {});
  const test = el("button", { class: "btn small" }, "test");
  test.onclick = async () => {
    test.disabled = true;
    result.replaceChildren(el("div", { class: "test-result" }, "asking the model…"));
    try { result.replaceChildren(probeResult(await api(`${url}/test`, { method: "POST" }))); }
    catch (err) { result.replaceChildren(el("div", { class: "test-result bad" }, `✗ ${err.message}`)); }
    test.disabled = false;
  };
  const del = el("button", { class: "btn small danger" }, "delete");
  del.onclick = () => act(del, async () => {
    if (!(await confirmDialog(`Delete decision model "${m.name}"?`, { confirmLabel: "delete" }))) return;
    savedToast(await api(url, { method: "DELETE" }), `decision model ${m.name} deleted`);
    await reload();
  });
  return el("div", { class: "panel mt", style: "background:var(--deck-2)", "data-decision-model": m.name },
    el("div", { class: "row spread" },
      el("div", {}, el("strong", {}, m.name), " ",
        el("span", { class: "muted small" }, `${m.endpoint} / ${m.model}`), " ",
        el("span", { class: "chip bare", title: m.images ? "takes images" : "text only" },
          m.images ? "text + images" : "text"),
        ...defaults.map((label) => el("span", { class: "chip", style: "margin-left:6px" }, label))),
      el("div", { class: "row" }, test, del)),
    result);
}

function addModelForm(d, reload) {
  const nameIn = el("input", { type: "text", placeholder: "name (e.g. jev)" });
  const epSel = el("select", {}, d.endpoints.map((ep) => el("option", {}, ep.name)));
  const modelIn = el("input", { type: "text", placeholder: "provider model id" });
  const imgSel = el("select", {}, el("option", { value: "" }, "protocol default"),
    el("option", { value: "true" }, "yes"), el("option", { value: "false" }, "no"));
  const prefill = () => {
    const ep = d.endpoints.find((e) => e.name === epSel.value);
    const preset = PRESETS.find((p) => ep && p.base_url === ep.base_url);
    if (preset?.model && !modelIn.value) modelIn.value = preset.model;
  };
  epSel.onchange = prefill; prefill();
  const save = el("button", { class: "btn primary" }, "add decision model");
  save.onclick = () => act(save, async () => {
    if (!nameIn.value.trim() || !modelIn.value.trim()) { toast("name it and give the model id"); return; }
    savedToast(await api("/api/settings/decision-models", { method: "POST", body: {
      name: nameIn.value.trim(), endpoint: epSel.value, model: modelIn.value.trim(),
      multimodal: imgSel.value === "" ? null : imgSel.value === "true" } }),
      `decision model ${nameIn.value.trim()} added`);
    await reload();
  });
  return el("details", { class: "panel mt" },
    el("summary", { style: "cursor:pointer;font-weight:600" }, "+ add decision model"),
    el("div", { class: "field-row mt" },
      el("label", { class: "field" }, el("span", {}, "name"), nameIn),
      el("label", { class: "field" }, el("span", {}, "endpoint"), epSel),
      el("label", { class: "field" }, el("span", {}, "model id"), modelIn),
      el("label", { class: "field" }, el("span", {}, "takes images"), imgSel)),
    el("div", { class: "row" }, save));
}

function defaultsEditor(d, reload) {
  const opts = (filter) => [el("option", { value: "" }, "none"),
    ...d.models.filter(filter).map((m) => el("option", {}, m.name))];
  const textSel = el("select", { "aria-label": "default decision model" }, opts(() => true));
  textSel.value = d.decision_model || "";
  const mediaSel = el("select", { "aria-label": "default for images" }, opts((m) => m.images));
  mediaSel.value = d.decision_media_model || "";
  const save = el("button", { class: "btn small primary" }, "save defaults");
  save.onclick = () => act(save, async () => {
    savedToast(await api("/api/settings/decision-defaults", { method: "PUT", body: {
      decision_model: textSel.value, decision_media_model: mediaSel.value } }), "decision defaults saved");
    await reload();
  });
  return el("div", { class: "panel mt" },
    el("div", { class: "small", style: "font-weight:600" }, "Defaults"),
    el("div", { class: "muted small", style: "margin:2px 0 6px" },
      "What a decide call uses when it names no model. A call that carries images goes to the ",
      "image default; blank means the default, which must then take images."),
    el("div", { class: "field-row" },
      el("label", { class: "field" }, el("span", {}, "default"), textSel),
      el("label", { class: "field" }, el("span", {}, "default for images"), mediaSel)),
    el("div", { class: "row" }, save));
}
