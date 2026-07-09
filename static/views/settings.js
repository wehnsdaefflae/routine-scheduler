// Settings: endpoint list, add/edit, delete, live test call. Direct model APIs only.

import { api } from "/static/api.js";
import { el, toast } from "/static/util.js";

export async function render(view) {
  view.append(el("div", { class: "page-head" }, el("h1", {}, "Settings")));

  // Test button + inline result for a git-remote input — surfaces reachability/auth errors
  // (e.g. a private repo before `gh auth login`) instead of failing silently later.
  function remoteTester(input) {
    const result = el("span", { style: "font-size:12px;font-family:var(--mono)" });
    const btn = el("button", { class: "btn small" }, "test");
    btn.onclick = async () => {
      const remote = input.value.trim();
      if (!remote) { result.style.color = ""; result.textContent = "enter a URL first"; return; }
      btn.disabled = true; result.style.color = ""; result.textContent = "testing…";
      try {
        const r = await api("/api/settings/test-remote", { method: "POST", body: { remote } });
        result.style.color = r.ok ? "var(--ok)" : "var(--err)";
        result.textContent = r.ok ? `✓ ${r.detail || "reachable"}` : `✗ ${r.error}`;
        result.title = r.detail || "";        // raw git error on hover

      } catch (err) { result.style.color = "var(--err)"; result.textContent = `✗ ${err.message}`; }
      btn.disabled = false;
    };
    return { btn, result };
  }

  // -- first-run setup banner -----------------------------------------------------
  const st = await api("/api/status").catch(() => ({}));
  if (st.needs_setup) {
    const banner = el("div", { class: "panel", style: "border-color:var(--warn);margin-bottom:14px" });
    const done = el("button", { class: "btn small primary" }, "finish setup");
    done.onclick = async () => {
      try { await api("/api/setup/complete", { method: "POST" }); toast("setup complete — no more first-run redirect"); banner.remove(); }
      catch (err) { toast(err.message, 5000); }
    };
    banner.append(
      el("strong", {}, "👋 First-run setup"),
      el("div", { class: "muted mt", style: "font-size:12.5px" },
        "Add a model provider (LLM endpoints, below), connect GitHub, and point at your repos — ",
        "Test each remote. When you're set:"),
      el("div", { class: "row mt" }, done));
    view.append(banner);
  }

  // -- GitHub connection (device flow — no container terminal) ---------------------
  view.append(el("h2", {}, "GitHub"));
  const ghBox = el("div", { class: "panel" });
  view.append(ghBox);
  async function renderGithub() {
    ghBox.innerHTML = "";
    let g;
    try { g = await api("/api/settings/github"); }
    catch (err) { ghBox.append(el("div", { class: "muted" }, err.message)); return; }
    if (!g.gh) { ghBox.append(el("div", { class: "muted" }, g.error || "gh CLI not available")); return; }
    ghBox.append(el("div", { class: "muted", style: "font-size:12.5px;margin-bottom:6px" },
      "Authorize GitHub so the scheduler can clone / pull / push your (private) library + source ",
      "repos. You enter a code on github.com in your own browser — no terminal needed."));
    const status = el("span", { style: "font-family:var(--mono);font-size:12.5px" });
    status.style.color = g.connected ? "var(--ok)" : "";
    status.textContent = g.connected ? `✓ connected as ${g.login}` : "not connected";
    const connect = el("button", { class: "btn small primary" }, g.connected ? "reconnect" : "connect GitHub");
    const flowArea = el("div", { class: "mt" });
    connect.onclick = async () => {
      connect.disabled = true; flowArea.innerHTML = "";
      let f;
      try { f = await api("/api/settings/github/device-start", { method: "POST" }); }
      catch (err) { toast(err.message, 6000); connect.disabled = false; return; }
      const wait = el("div", { class: "muted mt" }, "waiting for you to authorize…");
      flowArea.append(el("div", { class: "panel", style: "border-color:var(--warn)" },
        el("div", {}, "1. Open ",
          el("a", { href: f.verification_uri, target: "_blank", rel: "noopener" }, f.verification_uri)),
        el("div", { class: "mt" }, "2. Enter code: ",
          el("code", { style: "font-size:18px;letter-spacing:2px;user-select:all" }, f.user_code),
          el("button", { class: "btn small", style: "margin-left:8px",
            onclick: () => { navigator.clipboard?.writeText(f.user_code); toast("code copied"); } }, "copy")),
        wait));
      const deadline = Date.now() + (f.expires_in || 900) * 1000;
      const tick = async () => {
        if (Date.now() > deadline) { wait.style.color = "var(--err)"; wait.textContent = "code expired — try again"; connect.disabled = false; return; }
        let p;
        try { p = await api("/api/settings/github/device-poll", { method: "POST", body: { flow_id: f.flow_id } }); }
        catch (err) { wait.style.color = "var(--err)"; wait.textContent = `✗ ${err.message}`; connect.disabled = false; return; }
        if (p.status === "connected") { toast(`GitHub connected as ${p.login}`); connect.disabled = false; renderGithub(); return; }
        if (p.status === "error") { wait.style.color = "var(--err)"; wait.textContent = `✗ ${p.error}`; connect.disabled = false; return; }
        setTimeout(tick, (f.interval || 5) * 1000);
      };
      setTimeout(tick, (f.interval || 5) * 1000);
    };
    ghBox.append(el("div", { class: "row", style: "margin:6px 0" }, status, connect), flowArea);
  }
  await renderGithub();

  // -- central secrets store ------------------------------------------------------
  view.append(el("h2", {}, "Secrets"));
  const secBox = el("div", { class: "panel" });
  view.append(secBox);
  async function renderSecrets() {
    secBox.innerHTML = "";
    let s;
    try { s = await api("/api/settings/secrets"); }
    catch (err) { secBox.append(el("div", { class: "muted" }, err.message)); return; }
    secBox.append(el("div", { class: "muted", style: "font-size:12.5px;margin-bottom:6px" },
      "One store for every credential — injected into all utils, LLM endpoints, and the Claude ",
      "subscription (as CLAUDE_CODE_OAUTH_TOKEN) at run time. Values are write-only — never shown back."));

    const keyIn = el("input", { type: "text", placeholder: "KEY (e.g. CLAUDE_CODE_OAUTH_TOKEN)", style: "flex:1" });
    const valIn = el("input", { type: "password", placeholder: "value", style: "flex:1" });
    const delBtn = (k) => {
      const b = el("button", { class: "btn small danger" }, "delete");
      b.onclick = async () => {
        if (!confirm(`Delete secret ${k}?`)) return;
        try { await api(`/api/settings/secrets/${encodeURIComponent(k)}`, { method: "DELETE" }); renderSecrets(); }
        catch (err) { toast(err.message); }
      };
      return b;
    };

    // What the installed utils DECLARE they need — so you know exactly what to add (unset flagged).
    if (s.needed?.length) {
      secBox.append(el("div", { class: "mt", style: "font-size:12.5px;font-weight:600" }, "Needed by installed utils"));
      secBox.append(el("table", { class: "list" }, el("tbody", {}, s.needed.map((n) => {
        const setBtn = el("button", { class: "btn small" }, n.set ? "replace" : "set");
        setBtn.onclick = () => { keyIn.value = n.key; valIn.value = ""; valIn.focus(); };
        return el("tr", {},
          el("td", { class: "mono" }, n.key),
          el("td", { style: `font-size:12px;color:${n.set ? "var(--ok)" : "var(--warn)"}` }, n.set ? "✓ set" : "unset"),
          el("td", { class: "muted", style: "font-size:11.5px" }, n.utils.join(", ")),
          el("td", {}, n.set ? delBtn(n.key) : setBtn));
      }))));
    }

    // Anything you've set that no util declares (e.g. an endpoint's key_var, or a manual add).
    const declared = new Set((s.needed || []).map((n) => n.key));
    const extra = s.keys.filter((k) => !declared.has(k));
    if (extra.length) {
      secBox.append(el("div", { class: "mt", style: "font-size:12.5px;font-weight:600" }, "Other secrets set"));
      secBox.append(el("table", { class: "list" }, el("tbody", {}, extra.map((k) =>
        el("tr", {}, el("td", { class: "mono" }, k), el("td", { class: "muted" }, "••••••••"), el("td", {}, delBtn(k)))))));
    }
    if (!s.needed?.length && !extra.length)
      secBox.append(el("div", { class: "muted mt", style: "font-size:12px" }, "no secrets set yet"));

    const save = el("button", { class: "btn small primary" }, "set");
    save.onclick = async () => {
      const key = keyIn.value.trim();
      if (!key || !valIn.value) { toast("enter a KEY and a value"); return; }
      try {
        await api("/api/settings/secrets", { method: "PUT", body: { key, value: valIn.value } });
        toast(`${key} saved`); keyIn.value = ""; valIn.value = ""; renderSecrets();
      } catch (err) { toast(err.message, 5000); }
    };
    secBox.append(el("div", { class: "row mt" }, keyIn, valIn, save));
  }
  await renderSecrets();

  // -- library repositories -------------------------------------------------------
  view.append(el("h2", {}, "Library repositories"));
  const libBox = el("div", { class: "panel" });
  view.append(libBox);
  try {
    const { libraries } = await api("/api/settings/libraries");
    libBox.append(el("div", { class: "muted", style: "font-size:12.5px;margin-bottom:6px" },
      "Workflows, fragments, and utils each live in a git repo on your account. Not set up yet? ",
      "Clone your existing repo, or create a new private one seeded with the built-in defaults. ",
      "(Connect GitHub above first.)"));
    for (const lib of libraries) {
      if (!lib.provisioned) {
        const repoIn = el("input", { type: "text", placeholder: "owner/name or full URL", style: "flex:1" });
        const cloneB = el("button", { class: "btn small" }, "clone existing");
        const createB = el("button", { class: "btn small primary" }, "create + seed");
        const doProv = async (mode) => {
          const repo = repoIn.value.trim();
          if (!repo) { toast("enter a repo (owner/name or URL)"); return; }
          cloneB.disabled = createB.disabled = true;
          try {
            await api(`/api/settings/libraries/${lib.name}/provision`, { method: "POST", body: { repo, mode } });
            toast(`${lib.name}: ${mode === "clone" ? "cloned" : "created + seeded"}`); location.reload();
          } catch (err) { toast(err.message, 7000); cloneB.disabled = createB.disabled = false; }
        };
        cloneB.onclick = () => doProv("clone");
        createB.onclick = () => doProv("create");
        libBox.append(el("div", { class: "row", style: "margin:9px 0" },
          el("span", { class: "ref-tag", style: "min-width:90px;text-align:center" }, lib.name),
          repoIn, cloneB, createB));
        libBox.append(el("div", { class: "muted", style: "margin:-4px 0 8px 98px;font-size:11px" },
          "not set up yet"));
        continue;
      }
      const input = el("input", { type: "text", value: lib.remote || "",
        placeholder: "https://github.com/<you>/<repo>.git — empty = local only" });
      const save = el("button", { class: "btn small primary" }, "save + push");
      save.onclick = async () => {
        try { const r = await api(`/api/settings/libraries/${lib.name}`, { method: "PUT", body: { remote: input.value.trim() } });
          toast(r.pushed ? `${lib.name}: saved + pushed` : r.push_error ? `${lib.name}: saved (push failed: ${r.push_error})` : `${lib.name}: saved`); }
        catch (err) { toast(err.message, 5000); }
      };
      const t = remoteTester(input);
      libBox.append(el("div", { class: "row", style: "margin:9px 0" },
        el("span", { class: "ref-tag", style: "min-width:90px;text-align:center" }, lib.name),
        input, t.btn, save));
      libBox.append(el("div", { style: "margin:-4px 0 8px 98px" }, t.result));
    }
  } catch (err) { libBox.append(el("div", { class: "muted" }, err.message)); }

  // -- scheduler source repository (self-audit's push target) ---------------------
  view.append(el("h2", {}, "Source repository"));
  const srcBox = el("div", { class: "panel" });
  view.append(srcBox);
  try {
    const src = await api("/api/settings/source");
    srcBox.append(el("div", { class: "muted", style: "font-size:12.5px;margin-bottom:6px" },
      "The scheduler's own code repo — where the self-audit routine commits and pushes its changes. ",
      "Set the remote to the fork those autonomous pushes should target."));
    const input = el("input", { type: "text", value: src.remote || "",
      placeholder: "https://github.com/<you>/routine-scheduler.git — empty = local only" });
    const save = el("button", { class: "btn small primary" }, "save + push");
    save.onclick = async () => {
      try {
        const r = await api("/api/settings/source", { method: "PUT", body: { remote: input.value.trim() } });
        toast(r.pushed ? "source: saved + pushed"
          : r.push_error ? `source: saved (push failed: ${r.push_error})` : "source: saved");
      } catch (err) { toast(err.message, 5000); }
    };
    const t = remoteTester(input);
    srcBox.append(el("div", { class: "row", style: "margin:9px 0" },
      el("span", { class: "ref-tag", style: "min-width:90px;text-align:center" }, src.branch),
      input, t.btn, save));
    srcBox.append(el("div", { style: "margin:-4px 0 8px 98px" }, t.result));
    srcBox.append(el("div", { class: "muted", style: "font-family:var(--mono);font-size:11px" },
      src.home + (src.exists ? "" : "  ⚠ not a git repo")));
  } catch (err) { srcBox.append(el("div", { class: "muted" }, err.message)); }

  // -- endpoints ------------------------------------------------------------------
  view.append(el("h2", {}, "LLM endpoints"),
    el("div", { class: "muted", style: "margin-bottom:8px;font-size:12.5px" },
      "Model transports only (openai-compatible / anthropic / claude-cli). The scheduler is the ",
      "only harness. Keys live in ~/.credentials/*.env."));
  const listBox = el("div", {});
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
    // claude-cli uses the SUBSCRIPTION token from the Secrets store (so `gu claude` gets it too),
    // not a per-endpoint key. openai/anthropic take an inline key here OR their key_var from Secrets.
    let keyRow;
    if (ep.kind === "claude-cli") {
      keyRow = el("div", { class: "muted mt", style: "font-size:12px" },
        "Uses your Claude subscription — set ", el("code", {}, "CLAUDE_CODE_OAUTH_TOKEN"),
        " in Secrets above (mint it with ", el("code", {}, "claude setup-token"),
        "). Feeds this endpoint and ", el("code", {}, "gu claude"), ".");
    } else {
      const keyInput = el("input", { type: "password", style: "flex:1",
        placeholder: ep.has_inline_key ? "saved ✓ — paste to replace"
          : `paste API key (or set ${ep.key_var || "its key"} in Secrets)` });
      const saveKey = el("button", { class: "btn small primary" }, "save key");
      saveKey.onclick = async () => {
        if (!keyInput.value.trim()) { toast("paste a key first"); return; }
        try {
          await api(`/api/settings/endpoints/${ep.name}`, { method: "PUT", body: {
            name: ep.name, kind: ep.kind, base_url: ep.base_url || "",
            key_env_file: ep.key_env_file || "", key_var: ep.key_var || "",
            schema_mode: ep.schema_mode, context_chars: ep.context_chars,
            api_key: keyInput.value.trim(),
          }});
          toast(`${ep.name}: key saved`); keyInput.value = ""; await load();
        } catch (err) { toast(err.message, 5000); }
      };
      keyRow = el("div", { class: "row mt" }, keyInput, saveKey);
    }
    return el("div", { class: "panel mt" },
      el("div", { class: "row spread" },
        el("div", {},
          el("strong", {}, ep.name), " ",
          el("span", { class: "chip" }, ep.kind), " ",
          el("span", { class: "muted mono" }, ep.base_url || "")),
        delBtn),
      el("div", { class: "muted", style: "font-size:12px" },
        `schema_mode=${ep.schema_mode} · context_chars=${ep.context_chars}` +
        (ep.key_env_file ? ` · key file: ${ep.key_var} @ ${ep.key_env_file}` : "") +
        (ep.has_inline_key ? " · 🔑 key set" : "")),
      keyRow,
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
