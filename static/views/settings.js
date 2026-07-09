// Settings: endpoint list, add/edit, delete, live test call. Direct model APIs only.

import { api } from "/static/api.js";
import { el, toast } from "/static/util.js";

export async function render(view) {
  view.append(el("h1", {}, "Settings — LLM endpoints"),
    el("div", { class: "muted" },
      "Endpoints are model transports: openai-compatible / anthropic APIs, or claude-cli ",
      "(subscription-billed `claude -p`, fully stripped — no tools, our system prompt). The ",
      "scheduler stays the only harness. Keys live in ~/.credentials/*.env."));
  const listBox = el("div", { class: "mt" });
  view.append(listBox);

  async function load() {
    const data = await api("/api/settings/endpoints");
    listBox.innerHTML = "";
    for (const ep of data.endpoints) listBox.append(item(ep, data.default_roles));
    listBox.append(addForm());
    const roles = Object.entries(data.default_roles)
      .map(([r, v]) => `${r} → ${v.endpoint}:${v.model}`).join("  ·  ");
    listBox.append(el("div", { class: "muted mt" }, `default roles: ${roles || "(none)"}`));
  }

  function item(ep, roles) {
    const modelGuess = Object.values(roles).find((r) => r.endpoint === ep.name)?.model || "";
    const modelInput = el("input", { type: "text", value: modelGuess, placeholder: "model id", style: "width:220px" });
    const result = el("span", { class: "muted" });
    const testBtn = el("button", { class: "btn small" }, "test");
    testBtn.onclick = async () => {
      if (!modelInput.value.trim()) { toast("enter a model id to test"); return; }
      testBtn.disabled = true;
      result.textContent = "…";
      try {
        const r = await api(`/api/settings/endpoints/${ep.name}/test`,
                            { method: "POST", body: { model: modelInput.value.trim() } });
        result.textContent = r.ok
          ? `✓ ${r.latency_ms}ms · schema ${r.schema_ok ? "ok" : "VIOLATED"} · answer=${r.answer}`
          : `✗ ${r.error}`;
        result.style.color = r.ok && r.schema_ok ? "var(--ok)" : "var(--err)";
      } catch (err) { result.textContent = `✗ ${err.message}`; result.style.color = "var(--err)"; }
      testBtn.disabled = false;
    };
    const delBtn = el("button", { class: "btn small danger" }, "delete");
    delBtn.onclick = async () => {
      if (!confirm(`Delete endpoint "${ep.name}" from config.yaml?`)) return;
      try { await api(`/api/settings/endpoints/${ep.name}`, { method: "DELETE" }); await load(); }
      catch (err) { toast(err.message); }
    };
    return el("div", { class: "panel mt" },
      el("div", { class: "row spread" },
        el("div", {},
          el("strong", {}, ep.name), " ",
          el("span", { class: "chip" }, ep.kind), " ",
          el("span", { class: "muted mono" }, ep.base_url || "")),
        delBtn),
      el("div", { class: "muted", style: "font-size:12px" },
        `schema_mode=${ep.schema_mode} · context_chars=${ep.context_chars}` +
        (ep.key_env_file ? ` · key: ${ep.key_var} @ ${ep.key_env_file}` : "") +
        (ep.has_inline_key ? " · inline key" : "")),
      el("div", { class: "row mt" }, modelInput, testBtn, result));
  }

  function addForm() {
    const f = {
      name: el("input", { type: "text", placeholder: "name (e.g. vllm-box)" }),
      kind: el("select", {}, el("option", {}, "openai"), el("option", {}, "anthropic"),
               el("option", {}, "claude-cli")),
      base_url: el("input", { type: "text", placeholder: "https://host/v1" }),
      key_env_file: el("input", { type: "text", placeholder: "~/.credentials/foo.env (optional)" }),
      key_var: el("input", { type: "text", placeholder: "KEY_VAR (optional)" }),
      schema_mode: el("select", {}, el("option", {}, "json_schema"), el("option", {}, "json_object"),
                      el("option", {}, "none")),
      context_chars: el("input", { type: "number", value: "100000" }),
    };
    const save = el("button", { class: "btn primary" }, "add endpoint");
    save.onclick = async () => {
      try {
        await api("/api/settings/endpoints", { method: "POST", body: {
          name: f.name.value.trim(), kind: f.kind.value, base_url: f.base_url.value.trim(),
          key_env_file: f.key_env_file.value.trim(), key_var: f.key_var.value.trim(),
          schema_mode: f.schema_mode.value, context_chars: Number(f.context_chars.value) || 100000,
        }});
        toast("endpoint added");
        await load();
      } catch (err) { toast(err.message); }
    };
    return el("details", { class: "panel mt" },
      el("summary", { style: "cursor:pointer;font-weight:600" }, "+ add endpoint"),
      el("div", { class: "field-row mt" },
        el("label", { class: "field" }, el("span", {}, "name"), f.name),
        el("label", { class: "field" }, el("span", {}, "kind"), f.kind),
        el("label", { class: "field" }, el("span", {}, "base_url"), f.base_url)),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "key env file"), f.key_env_file),
        el("label", { class: "field" }, el("span", {}, "key var"), f.key_var),
        el("label", { class: "field" }, el("span", {}, "schema mode"), f.schema_mode),
        el("label", { class: "field" }, el("span", {}, "context chars"), f.context_chars)),
      el("div", { class: "row" }, save));
  }

  await load();
}
