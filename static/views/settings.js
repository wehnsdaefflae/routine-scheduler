// Settings: GitHub device flow, secrets store, library repos, source repo, server restart.
// The LLM endpoints section (CRUD + system model + live test) lives in settings-endpoints.js.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { setQuery } from "/static/router.js";
import { el, skeleton, toast, when } from "/static/util.js";
import { renderEndpoints } from "/static/views/settings-endpoints.js";
import * as notify from "/static/notify.js";

export async function render(view, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / configuration"),
      el("h1", {}, "Settings"))));

  // Section nav — a visible location indicator within Settings; the active sub-section is in the
  // URL (#/settings?section=endpoints), so a deep link / reload lands on the same section.
  // Endpoints (with the model catalog + system model) leads: it is the first-run critical path.
  const SECTIONS = [["endpoints", "Endpoints"], ["github", "GitHub"],
                    ["connections", "Connections"], ["secrets", "Secrets"],
                    ["libraries", "Library"], ["library-sync", "Library sync"],
                    ["source", "Source"], ["server", "Server"],
                    ["notifications", "Notifications"]];
  const secNav = el("div", { class: "filterbar" });
  view.append(secNav);
  const sectionHead = (id, title) => el("h2", { id: `sec-${id}` }, title);
  const goSection = (id, smooth = true) => {
    setQuery({ section: id });
    document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
    [...secNav.children].forEach((b) => b.dataset && b.classList.toggle("on", b.dataset.sec === id));
  };
  secNav.append(el("span", { class: "lbl" }, "section"));
  for (const [id, label] of SECTIONS) {
    const b = el("span", { class: "tag click", onclick: () => goSection(id) }, label);
    b.dataset.sec = id;
    secNav.append(b);
  }

  // Test button + inline result for a git-remote input — surfaces reachability/auth errors
  // (e.g. a private repo before `gh auth login`) instead of failing silently later.
  function remoteTester(input) {
    const result = el("span", { class: "small mono" });
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
    const banner = el("div", { class: "panel warn", style: "margin-bottom:14px" });
    const done = el("button", { class: "btn small primary" }, "finish setup");
    done.onclick = async () => {
      try { await api("/api/setup/complete", { method: "POST" }); toast("setup complete — no more first-run redirect"); banner.remove(); }
      catch (err) { toast(err.message, 5000, { error: true }); }
    };
    banner.append(
      el("strong", {}, "First-run setup"),
      el("div", { class: "muted mt small" },
        "Add a model provider (LLM endpoints, at the top), connect GitHub, and point at your repos — ",
        "Test each remote. When you're set:"),
      el("div", { class: "row mt" }, done));
    view.append(banner);
  }

  // -- LLM endpoints + model catalog + system model (settings-endpoints.js) --------
  // First in the DOM: without an endpoint, a model, and the system model nothing else works.
  // renderEndpoints appends its containers synchronously, so calling it un-awaited keeps the
  // DOM order while its data loads in parallel with the sections below.
  view.append(sectionHead("endpoints", "LLM endpoints"));
  const endpointsReady = renderEndpoints(view);

  // -- GitHub connection (device flow — no container terminal) ---------------------
  view.append(sectionHead("github", "GitHub"));
  const ghBox = el("div", { class: "panel" });
  ghBox.append(skeleton(["50%", "80%"]));
  view.append(ghBox);
  async function renderGithub() {
    let g;
    try { g = await api("/api/settings/github"); }
    catch (err) { ghBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    ghBox.replaceChildren();
    if (!g.gh) { ghBox.append(el("div", { class: "muted" }, g.error || "gh CLI not available")); return; }
    ghBox.append(el("div", { class: "muted small", style: "margin-bottom:6px" },
      "Authorize GitHub so the scheduler can clone / pull / push your (private) library + source ",
      "repos. You enter a code on github.com in your own browser — no terminal needed."));
    const status = el("span", { class: "small mono" });
    status.style.color = g.connected ? "var(--ok)" : "";
    status.textContent = g.connected ? `✓ connected as ${g.login}` : "not connected";
    const connect = el("button", { class: "btn small primary" }, g.connected ? "reconnect" : "connect GitHub");
    const flowArea = el("div", { class: "mt" });

    // Render + poll an active device flow. `f` = {flow_id, user_code, verification_uri, interval,
    // expires_in}. The flow_id is kept in the URL (?flow=) so a reload resumes the SAME code.
    function runFlow(f) {
      connect.disabled = true;
      flowArea.replaceChildren();
      const wait = el("div", { class: "muted mt" }, "waiting for you to authorize…");
      flowArea.append(el("div", { class: "panel warn" },
        el("div", {}, "1. Open ",
          el("a", { href: f.verification_uri, target: "_blank", rel: "noopener" }, f.verification_uri)),
        el("div", { class: "mt" }, "2. Enter code: ",
          el("code", { style: "font-size:18px;letter-spacing:2px;user-select:all" }, f.user_code),
          el("button", { class: "btn small", style: "margin-left:8px",
            onclick: () => { navigator.clipboard?.writeText(f.user_code); toast("code copied"); } }, "copy")),
        wait));
      const stop = (msg, color) => { wait.style.color = color; wait.textContent = msg; connect.disabled = false; setQuery({ flow: "" }); };
      const deadline = Date.now() + (f.expires_in || 900) * 1000;
      const tick = async () => {
        if (Date.now() > deadline) { stop("code expired — try again", "var(--err)"); return; }
        let p;
        try { p = await api("/api/settings/github/device-poll", { method: "POST", body: { flow_id: f.flow_id } }); }
        catch (err) { stop(`✗ ${err.message}`, "var(--err)"); return; }
        if (p.status === "connected") { toast(`GitHub connected as ${p.login}`); connect.disabled = false; setQuery({ flow: "" }); renderGithub(); return; }
        if (p.status === "error") { stop(`✗ ${p.error}`, "var(--err)"); return; }
        setTimeout(tick, (f.interval || 5) * 1000);
      };
      setTimeout(tick, (f.interval || 5) * 1000);
    }

    connect.onclick = async () => {
      connect.disabled = true; flowArea.replaceChildren();
      let f;
      try { f = await api("/api/settings/github/device-start", { method: "POST" }); }
      catch (err) { toast(err.message, 6000, { error: true }); connect.disabled = false; return; }
      setQuery({ section: "github", flow: f.flow_id });   // make the connect-in-progress addressable
      runFlow(f);
    };
    ghBox.append(el("div", { class: "row", style: "margin:6px 0" }, status, connect), flowArea);

    // Resume an in-progress flow after a reload: fetch the still-valid code and pick polling back up.
    if (query.flow && !g.connected) {
      api(`/api/settings/github/device-flow/${query.flow}`)
        .then((f) => runFlow(f))
        .catch(() => setQuery({ flow: "" }));   // gone/expired → just show the connect button
    }
  }
  const githubReady = renderGithub();

  // -- OAuth connections (external accounts routines act on behalf of) --------------
  view.append(sectionHead("connections", "Connections"));
  const connBox = el("div", { class: "panel" });
  connBox.append(skeleton(["60%", "85%"]));
  view.append(connBox);
  async function renderConnections() {
    let d;
    try { d = await api("/api/settings/oauth"); }
    catch (err) { connBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    connBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
      "Connect external accounts (e.g. Notion) via OAuth so routines can act on your behalf. Bind a ",
      "connection on a routine's page; its access token is injected only into utils that declare it."));

    function disconnectBtn(provider, account) {
      const b = el("button", { class: "btn small danger" }, "disconnect");
      b.onclick = async () => {
        if (!(await confirmDialog(`Disconnect ${provider}:${account}?`, { confirmLabel: "disconnect" }))) return;
        try { await api(`/api/settings/oauth/${provider}/${encodeURIComponent(account)}`, { method: "DELETE" }); renderConnections(); }
        catch (err) { toast(err.message, 4000, { error: true }); }
      };
      return b;
    }

    // Auth-code + PKCE: authorize opens in a NEW tab (the provider redirects it to /oauth/callback);
    // this tab polls the flow until the callback reports connected.
    async function startConnect(providerId, account) {
      if (!account) { toast("enter an account label first"); return; }
      let f;
      try { f = await api(`/api/settings/oauth/${providerId}/authorize-start`, { method: "POST", body: { account } }); }
      catch (err) { toast(err.message, 6000, { error: true }); return; }
      window.open(f.authorize_url, "_blank", "noopener");
      toast("authorize in the new tab, then return here");
      const deadline = Date.now() + 600 * 1000;
      const tick = async () => {
        if (Date.now() > deadline) return;
        let p;
        try { p = await api(`/api/settings/oauth/flow/${f.flow_id}`); } catch { return; }
        if (p.status === "connected") { toast(`${providerId} connected`); renderConnections(); return; }
        if (p.status === "error") { toast(`✗ ${p.error || "authorization failed"}`, 6000, { error: true }); return; }
        setTimeout(tick, 2000);
      };
      setTimeout(tick, 2000);
    }

    // Public URL (public_url) — the BASE the provider redirects back to; the callback is derived
    // (this field is the base, NOT the callback path — the app appends /oauth/callback itself).
    // Pre-fill from THIS browser's origin (the URL you reached the console at) when it's https and
    // nothing is saved yet — so you almost never have to type it.
    const originGuess = location.origin.startsWith("https://") ? location.origin : "";
    const urlIn = el("input", { type: "text", placeholder: "https://host.ts.net", style: "flex:1", value: d.public_url || originGuess });
    const urlSave = el("button", { class: "btn small" }, "save");
    urlSave.onclick = async () => {
      try { await api("/api/settings/oauth/public-url", { method: "PUT", body: { public_url: urlIn.value.trim() } }); toast("public URL saved"); renderConnections(); }
      catch (err) { toast(err.message, 5000, { error: true }); }
    };
    const callbackLine = el("div", { class: "small mt" });
    if (d.public_url) {
      const cb = `${d.public_url.replace(/\/+$/, "")}/oauth/callback`;
      callbackLine.append(
        el("span", { class: "muted" }, "Register this exact callback in each provider's OAuth app: "),
        el("code", { "data-conn-callback": "", style: "user-select:all" }, cb),
        el("button", { class: "btn small", style: "margin-left:8px",
          onclick: () => { navigator.clipboard?.writeText(cb); toast("callback URL copied"); } }, "copy"));
    }
    connBox.append(
      el("div", { class: "mt small", style: "font-weight:600" }, "Public URL"),
      el("div", { class: "muted small" },
        "Your instance's external https BASE url (e.g. your Tailscale Serve URL) — the base, ",
        "not a path. Providers redirect back to ", el("code", {}, "<this>/oauth/callback"), "."),
      el("div", { class: "row mt", "data-conn-url": "" }, urlIn, urlSave),
      (!d.public_url && originGuess)
        ? el("div", { class: "muted small mt" }, "Pre-filled from this browser's address (",
            el("code", {}, originGuess), ") — click save to use it.")
        : null,
      callbackLine);

    // Providers — connect a new account (disabled until the redirect URL + the app creds are set).
    connBox.append(el("div", { class: "mt small", style: "font-weight:600" }, "Providers"));
    for (const p of d.providers) {
      const acct = el("input", { type: "text", placeholder: "account label", style: "width:150px" });
      const connect = el("button", { class: "btn small primary" }, "connect");
      connect.disabled = !(p.configured && d.public_url_set);
      connect.onclick = () => startConnect(p.id, acct.value.trim());
      // Straight link to where you create the OAuth app for this provider (its dev console).
      const consoleLink = p.console_url
        ? el("a", { href: p.console_url, target: "_blank", rel: "noopener", class: "small",
            "data-provider-link": p.id, title: `create the ${p.name} OAuth app` }, "create app ↗")
        : null;
      const status = p.configured
        ? el("span", { class: "small", style: "color:var(--ok)" }, "✓ app configured")
        : el("span", { class: "small", style: "color:var(--warn)" }, `set ${p.client_id_key} + secret in Secrets`);
      connBox.append(el("div", { class: "row", style: "margin:4px 0", "data-provider": p.id },
        el("span", { style: "width:82px;font-weight:600" }, p.name), consoleLink, status, acct, connect));
    }
    if (!d.public_url_set)
      connBox.append(el("div", { class: "muted small mt" }, "set the redirect URL above to enable connecting"));

    // Connected accounts.
    connBox.append(el("div", { class: "mt small", style: "font-weight:600" }, "Connected accounts"));
    if (!d.connections.length) connBox.append(el("div", { class: "muted small", "data-conn-empty": "" }, "none yet"));
    else connBox.append(el("div", { class: "tablewrap" }, el("table", { class: "list" }, el("tbody", {},
      d.connections.map((c) => el("tr", { "data-conn": `${c.provider}:${c.account}` },
        el("td", {}, `${c.provider}:${c.account}`),
        el("td", { class: "muted small" }, c.label || ""),
        el("td", { class: "small", style: `color:${c.needs_reauth ? "var(--warn)" : "var(--ok)"}` },
          c.needs_reauth ? "needs re-auth" : "ok"),
        el("td", {}, disconnectBtn(c.provider, c.account))))))));
  }
  const connectionsReady = renderConnections();

  // -- central secrets store ------------------------------------------------------
  view.append(sectionHead("secrets", "Secrets"));
  const secBox = el("div", { class: "panel" });
  secBox.append(skeleton(["60%", "90%"]));
  view.append(secBox);
  async function renderSecrets() {
    let s;
    try { s = await api("/api/settings/secrets"); }
    catch (err) { secBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    secBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
      "One store for every credential — injected into all utils, LLM endpoints, and the Claude ",
      "subscription (as CLAUDE_CODE_OAUTH_TOKEN) at run time. Values are write-only — never shown back."));

    const keyIn = el("input", { type: "text", placeholder: "KEY (e.g. CLAUDE_CODE_OAUTH_TOKEN)", style: "flex:1" });
    const valIn = el("input", { type: "password", placeholder: "value", style: "flex:1" });
    const delBtn = (k) => {
      const b = el("button", { class: "btn small danger" }, "delete");
      b.onclick = async () => {
        if (!(await confirmDialog(`Delete secret ${k}?`, { confirmLabel: "delete" }))) return;
        try { await api(`/api/settings/secrets/${encodeURIComponent(k)}`, { method: "DELETE" }); renderSecrets(); }
        catch (err) { toast(err.message, 4000, { error: true }); }
      };
      return b;
    };

    // What the installed utils DECLARE they need — so you know exactly what to add (unset flagged).
    if (s.needed?.length) {
      secBox.append(el("div", { class: "mt small", style: "font-weight:600" }, "Needed by installed utils"));
      secBox.append(el("div", { class: "tablewrap" },
        el("table", { class: "list" }, el("tbody", {}, s.needed.map((n) => {
          const setBtn = el("button", { class: "btn small" }, n.set ? "replace" : "set");
          setBtn.onclick = () => { keyIn.value = n.key; valIn.value = ""; valIn.focus(); };
          // The declaring util's usage + docstring — shows the expected FORMAT of a structured
          // secret (e.g. FTP_SOURCES's JSON shape) right where you set it.
          const fmt = (n.doc || n.usage)
            ? el("details", { class: "small", "data-secret-fmt": n.key },
                el("summary", { class: "muted", style: "cursor:pointer" }, "format / help"),
                el("pre", { style: "white-space:pre-wrap;font-size:11px;margin:4px 0;padding:6px 8px;background:var(--ink);border:1px solid var(--line);border-radius:6px" },
                  [n.usage, n.doc].filter(Boolean).join("\n\n")))
            : null;
          return el("tr", {},
            el("td", {}, el("div", { class: "mono" }, n.key), fmt),
            el("td", { class: "small", style: `color:${n.set ? "var(--ok)" : "var(--warn)"}` }, n.set ? "✓ set" : "unset"),
            el("td", { class: "muted small" }, n.utils.join(", ")),
            el("td", {}, n.set ? delBtn(n.key) : setBtn));
        })))));
    }

    // Anything you've set that no util declares (e.g. an endpoint's key_var, or a manual add).
    const declared = new Set((s.needed || []).map((n) => n.key));
    const extra = s.keys.filter((k) => !declared.has(k));
    if (extra.length) {
      secBox.append(el("div", { class: "mt small", style: "font-weight:600" }, "Other secrets set"));
      secBox.append(el("div", { class: "tablewrap" },
        el("table", { class: "list" }, el("tbody", {}, extra.map((k) =>
          el("tr", {}, el("td", {}, k), el("td", { class: "muted" }, "••••••••"), el("td", {}, delBtn(k))))))));
    }
    if (!s.needed?.length && !extra.length)
      secBox.append(el("div", { class: "muted mt small" }, "no secrets set yet"));

    const save = el("button", { class: "btn small primary" }, "set");
    save.onclick = async () => {
      const key = keyIn.value.trim();
      if (!key || !valIn.value) { toast("enter a KEY and a value"); return; }
      try {
        await api("/api/settings/secrets", { method: "PUT", body: { key, value: valIn.value } });
        toast(`${key} saved`); keyIn.value = ""; valIn.value = ""; renderSecrets();
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    // show/hide the value while typing — a JSON map is unreadable when masked
    const showVal = el("button", { class: "btn small", type: "button" }, "show");
    showVal.onclick = () => {
      const masked = valIn.type === "password";
      valIn.type = masked ? "text" : "password";
      showVal.textContent = masked ? "hide" : "show";
    };
    secBox.append(el("div", { class: "row mt" }, keyIn, valIn, showVal, save));

    // -- multi-entry (JSON-map) secrets: manage one entry at a time -----------------------
    // The store never returns values, so you extend a map secret (e.g. FTP_SOURCES) by adding a
    // single entry — the other entries' values are never shown or re-typed.
    const maps = s.maps || {};
    secBox.append(el("div", { class: "mt small", style: "font-weight:600" }, "Multi-entry secrets (JSON maps)"));
    const mapKeys = Object.keys(maps);
    if (mapKeys.length) {
      for (const k of mapKeys) {
        const chips = maps[k].map((name) => {
          const x = el("button", { class: "btn small danger", title: `delete ${name}` }, `${name} ✕`);
          x.onclick = async () => {
            if (!(await confirmDialog(`Delete entry “${name}” from ${k}?`, { confirmLabel: "delete" }))) return;
            try { await api(`/api/settings/secrets/${encodeURIComponent(k)}/entry/${encodeURIComponent(name)}`, { method: "DELETE" }); renderSecrets(); }
            catch (err) { toast(err.message, 4000, { error: true }); }
          };
          return x;
        });
        secBox.append(el("div", { class: "row", style: "margin:4px 0;flex-wrap:wrap", "data-map": k },
          el("span", { class: "mono small", style: "min-width:130px" }, k), ...chips));
      }
    } else {
      secBox.append(el("div", { class: "muted small" }, "none yet — add an entry below to start one (e.g. FTP_SOURCES)"));
    }
    const mKey = el("input", { type: "text", placeholder: "secret (e.g. FTP_SOURCES)", style: "flex:1", list: "secret-names", "data-map-entry": "key" });
    const mName = el("input", { type: "text", placeholder: "entry name (e.g. grantsforbina)", style: "flex:1", "data-map-entry": "name" });
    const mVal = el("textarea", { placeholder: '{"host": "…", "user": "…", "pass": "…"}', rows: "3", style: "width:100%;font-size:12px", "data-map-entry": "value" });
    const mSave = el("button", { class: "btn small primary" }, "add / replace entry");
    mSave.onclick = async () => {
      const key = mKey.value.trim(), name = mName.value.trim();
      if (!key || !name) { toast("enter a secret and an entry name"); return; }
      let value;
      try { value = JSON.parse(mVal.value); }
      catch { toast("the entry value must be valid JSON", 4000, { error: true }); return; }
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        toast('the entry value must be a JSON object, e.g. {"host": …}', 4000, { error: true }); return;
      }
      try {
        await api(`/api/settings/secrets/${encodeURIComponent(key)}/entry`, { method: "PUT", body: { name, value } });
        toast(`${key} · ${name} saved`); mName.value = ""; mVal.value = ""; renderSecrets();
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    secBox.append(
      el("datalist", { id: "secret-names" }, ...(s.needed || []).map((n) => el("option", { value: n.key }))),
      el("div", { class: "muted small mt" }, "Add or replace ONE entry of a JSON-map secret — the other entries stay untouched and their values are never shown."),
      el("div", { class: "row mt" }, mKey, mName),
      el("div", { class: "mt" }, mVal),
      el("div", { class: "row mt" }, mSave));
  }
  const secretsReady = renderSecrets();

  // -- the library repository ------------------------------------------------------
  view.append(sectionHead("libraries", "Library repository"));
  const libBox = el("div", { class: "panel" });
  libBox.append(skeleton(["60%", "90%"]));
  view.append(libBox);
  async function renderLibraries() {
    try {
      const { libraries } = await api("/api/settings/libraries");
      libBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
        "One git repo holds everything the instance acquires: workflows/, traits/, permissions/, utils/ ",
        "(with the gu dispatcher) — plus routines/ and sanitized config, exported by the scheduled ",
        "Library sync below. ",
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
            } catch (err) { toast(err.message, 7000, { error: true }); cloneB.disabled = createB.disabled = false; }
          };
          cloneB.onclick = () => doProv("clone");
          createB.onclick = () => doProv("create");
          libBox.append(el("div", { class: "row", style: "margin:9px 0" },
            el("span", { class: "ref-tag", style: "min-width:90px;text-align:center" }, lib.name),
            repoIn, cloneB, createB));
          libBox.append(el("div", { class: "faint small", style: "margin:-4px 0 8px 98px" },
            "not set up yet"));
          continue;
        }
        const input = el("input", { type: "text", value: lib.remote || "",
          placeholder: "https://github.com/<you>/<repo>.git — empty = local only" });
        const save = el("button", { class: "btn small primary" }, "save + push");
        save.onclick = async () => {
          try { const r = await api(`/api/settings/libraries/${lib.name}`, { method: "PUT", body: { remote: input.value.trim() } });
            toast(r.pushed ? `${lib.name}: saved + pushed` : r.push_error ? `${lib.name}: saved (push failed: ${r.push_error})` : `${lib.name}: saved`); }
          catch (err) { toast(err.message, 5000, { error: true }); }
        };
        const t = remoteTester(input);
        libBox.append(el("div", { class: "row", style: "margin:9px 0" },
          el("span", { class: "ref-tag", style: "min-width:90px;text-align:center" }, lib.name),
          input, t.btn, save));
        libBox.append(el("div", { style: "margin:-4px 0 8px 98px" }, t.result));
      }
    } catch (err) { libBox.replaceChildren(el("div", { class: "muted" }, err.message)); }
  }
  const librariesReady = renderLibraries();

  // -- scheduled library sync (a plain daemon job — the same commands every time) ---
  view.append(sectionHead("library-sync", "Library sync"));
  const lsBox = el("div", { class: "panel" });
  lsBox.append(skeleton(["50%", "80%"]));
  view.append(lsBox);
  async function renderLibrarySync() {
    let ls;
    try { ls = await api("/api/settings/library-sync"); }
    catch (err) { lsBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    const enabled = el("input", { type: "checkbox", ...(ls.enabled ? { checked: true } : {}) });
    const known = ["manual", "hourly", "daily", "weekly", "monthly"];
    const friendly = known.includes(ls.schedule_friendly?.frequency)
      ? ls.schedule_friendly : { frequency: "daily", time: "06:00" };
    const sched = scheduleEditor(friendly, st.server_tz || "");
    const save = el("button", { class: "btn small primary" }, "save");
    save.onclick = async () => {
      save.disabled = true;
      try {
        await api("/api/settings/library-sync", { method: "PUT",
          body: { enabled: enabled.checked, schedule: { friendly: sched.value() } } });
        toast("library sync schedule saved");
        renderLibrarySync();
      } catch (err) { toast(err.message, 5000, { error: true }); save.disabled = false; }
    };
    const runNow = el("button", { class: "btn small" }, "sync now");
    runNow.onclick = async () => {
      runNow.disabled = true; runNow.textContent = "syncing…";
      try {
        const r = await api("/api/settings/library-sync/run", { method: "POST" });
        toast(`library sync: ${r.status}${r.error ? ` — ${r.error}` : ""}`, r.status === "ok" ? 4000 : 8000,
              { error: r.status === "error" });
        renderLibrarySync();
      } catch (err) { toast(err.message, 7000, { error: true }); runNow.disabled = false; runNow.textContent = "sync now"; }
    };
    const lastLine = !ls.last
      ? "no sync has run yet"
      : `last sync ${ls.last.ts} — ${ls.last.status}`
        + (ls.last.error ? ` (${ls.last.error})`
           : ls.last.sync?.pull_error ? ` (pull conflict: ${ls.last.sync.pull_error})`
           : ls.last.sync ? ` (${ls.last.sync.pushed ? "pushed" : ls.last.sync.has_remote ? "push failed" : "no remote — committed locally"})`
           : "");
    lsBox.replaceChildren(
      el("div", { class: "muted small", style: "margin-bottom:6px" },
        "Mirrors the instance (routines + sanitized config) into the library repo and commits, ",
        "pulls and pushes it — the exact same commands every time, so it runs as a plain ",
        "scheduled job, not a routine."),
      el("label", { class: "row", style: "gap:8px;margin:9px 0" }, enabled,
        el("span", {}, "sync on a schedule")),
      sched.node,
      el("div", { class: "row", style: "gap:8px;margin-top:9px" }, save, runNow),
      el("div", { class: "faint small", style: "margin-top:8px" }, lastLine),
      el("div", { class: "faint small" },
        ls.enabled && ls.next_fire ? `next: ${ls.next_fire}` : "not scheduled"));
  }
  const librarySyncReady = renderLibrarySync();

  // -- scheduler source repository (self-audit's push target) ---------------------
  view.append(sectionHead("source", "Source repository"));
  const srcBox = el("div", { class: "panel" });
  srcBox.append(skeleton(["60%", "90%"]));
  view.append(srcBox);
  async function renderSource() {
    try {
      const src = await api("/api/settings/source");
      srcBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
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
        } catch (err) { toast(err.message, 5000, { error: true }); }
      };
      const t = remoteTester(input);
      srcBox.append(el("div", { class: "row", style: "margin:9px 0" },
        el("span", { class: "ref-tag", style: "min-width:90px;text-align:center" }, src.branch),
        input, t.btn, save));
      srcBox.append(el("div", { style: "margin:-4px 0 8px 98px" }, t.result));
      srcBox.append(el("div", { class: "faint small" },
        src.home + (src.exists ? "" : "  ⚠ not a git repo")));
    } catch (err) { srcBox.replaceChildren(el("div", { class: "muted" }, err.message)); }
  }
  const sourceReady = renderSource();

  // -- server process (graceful restart onto committed code) -----------------------
  view.append(sectionHead("server", "Server"));
  const srvBox = el("div", { class: "panel" });
  srvBox.append(skeleton(["50%", "80%"]));
  view.append(srvBox);
  async function renderServer() {
    let s;
    try { s = await api("/api/status"); }
    catch (err) { srvBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    srvBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
      "Restart the daemon to load committed code — the same graceful path the self-audit ",
      "routine uses: nothing new fires, active runs finish (a run parked on a question defers ",
      "the drain), then the process exits and its supervisor relaunches it. The console drops ",
      "out for a few seconds."));
    const statusLine = el("div", { class: "test-result" });
    const btn = el("button", { class: "btn small" }, "↻ restart server");
    const cancel = el("button", { class: "btn small ghost", hidden: true }, "cancel");
    const withdraw = el("button", { class: "btn small ghost", hidden: true }, "withdraw request");

    // After a request: poll until the process comes back with a different `started`.
    // Phases: pending (sentinel visible) → draining → down (fetch fails) → back up.
    async function watch(initialStarted) {
      btn.disabled = true;
      withdraw.hidden = false;
      const t0 = Date.now();
      while (Date.now() - t0 < 180000) {
        await new Promise((r) => setTimeout(r, 2000));
        let st;
        try { st = await api("/api/status"); }
        catch {
          withdraw.hidden = true;   // too late to withdraw — the process is already down
          statusLine.style.color = "";
          statusLine.textContent = "⟳ server is down — waiting for the supervisor to relaunch it…";
          continue;
        }
        if (st.started && st.started !== initialStarted) {
          toast("server restarted — running the committed code");
          renderServer();
          return;
        }
        if (!st.restart_requested) {  // withdrawn (here or elsewhere) and same process → resume
          statusLine.style.color = "";
          statusLine.textContent = "request withdrawn — no restart";
          btn.disabled = false; withdraw.hidden = true;
          return;
        }
        const n = Object.keys(st.active_runs || {}).length;
        statusLine.style.color = "";
        statusLine.textContent = st.draining
          ? `⟳ draining — ${n} active run${n === 1 ? "" : "s"} still finishing…`
          : n ? `⟳ requested — ${n} run${n === 1 ? "" : "s"} active (a parked run defers the drain)…`
              : "⟳ requested — restarting momentarily…";
      }
      statusLine.style.color = "var(--err)";
      statusLine.textContent = "✗ not back after 3 minutes — check the supervisor (docker logs / systemctl status)";
      btn.disabled = false; withdraw.hidden = true;
    }

    let armed = false;
    const disarm = () => {
      armed = false; cancel.hidden = true;
      btn.textContent = "↻ restart server"; btn.classList.remove("danger");
      statusLine.textContent = "";
    };
    cancel.onclick = disarm;
    btn.onclick = async () => {
      if (!armed) {   // two-step confirm, in place
        armed = true; cancel.hidden = false;
        btn.textContent = "confirm restart"; btn.classList.add("danger");
        statusLine.style.color = "";
        statusLine.textContent = "drains active runs, then the console goes down for a few seconds";
        return;
      }
      disarm();
      try {
        const r = await api("/api/settings/restart", { method: "POST" });
        statusLine.textContent = r.parked
          ? "⟳ requested — a run is parked waiting on you (see Decisions); the drain starts once nothing is parked"
          : "⟳ requested…";
        watch(s.started);
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    withdraw.onclick = async () => {
      try { await api("/api/settings/restart", { method: "DELETE" }); toast("restart request withdrawn"); }
      catch (err) { toast(err.message, 4000, { error: true }); }
    };

    srvBox.append(
      el("div", { class: "row", style: "margin:6px 0" },
        el("span", { class: "small mono muted" }, `v${s.version} · process up since `),
        s.started ? when(s.started) : el("span", { class: "muted small" }, "(unknown)"),
        btn, cancel, withdraw),
      statusLine);
    if (s.restart_requested) {   // a pending request survives a page reload — resume watching
      statusLine.textContent = "⟳ a restart is already requested…";
      watch(s.started);
    }
  }
  const serverReady = renderServer();

  // -- notifications (tier 1 + Web Push) --------------------------------------------
  view.append(sectionHead("notifications", "Notifications"));
  view.append(renderNotifications());

  // The section fills load in parallel — every panel was appended above in its final DOM
  // order, so each async render only fills its own box. Wait for all of them before the
  // deep-link jump so the anchor lands on settled heights.
  await Promise.all([endpointsReady, githubReady, connectionsReady, secretsReady, librariesReady,
                     librarySyncReady, sourceReady, serverReady]);

  // Land on the requested section (deep link / reload). Everything above is now in the DOM, so
  // the anchor exists; jump without smooth-scroll on first paint.
  if (query.section) goSection(query.section, false);
}

// ---- notifications: tier 1 (tab open) + tier 2 (Web Push, tab closed) -------------------------
function renderNotifications() {
  const panel = el("div", { class: "panel" });
  panel.append(el("div", { class: "muted small", style: "margin-bottom:8px" },
    "Both are OPT-IN and per browser. Tab-open notifications fire from this console while any ",
    "tab is open; Web Push reaches this browser even with the console closed — like the Discord ",
    "mirror, nothing is sent until you enable it here."));

  // tier 1 — the Notification API on the live event stream
  const t1box = el("input", { type: "checkbox", checked: notify.enabled() ? "" : null,
    disabled: notify.supported() ? null : "" });
  t1box.onchange = async () => {
    const on = await notify.setEnabled(t1box.checked);
    t1box.checked = on;
    toast(on ? "tab-open notifications enabled" :
      t1box.checked === false && Notification.permission === "denied"
        ? "the browser blocked notifications — allow them in the site settings"
        : "tab-open notifications off");
  };
  panel.append(el("label", { class: "row", style: "gap:8px" }, t1box,
    el("div", {},
      el("div", { class: "t-title" }, "OS notifications while a console tab is open"),
      el("div", { class: "muted small" }, notify.supported()
        ? "new decisions pop up in the system tray; clicking one opens the Decisions page"
        : "this browser has no Notification API"))));

  // tier 2 — Web Push through the service worker
  const pushRow = el("div", { class: "mt" }, el("span", { class: "muted small" }, "Web Push — checking…"));
  panel.append(pushRow);
  const renderPush = async () => {
    pushRow.replaceChildren();
    if (!window.isSecureContext) {
      pushRow.append(el("div", { class: "muted small" },
        "Web Push needs a secure context — serve the console over HTTPS (or open it via localhost, ",
        "e.g. an SSH tunnel) to enable push with the browser closed."));
      return;
    }
    const st = await notify.pushStatus();
    if (!st.supported) {
      pushRow.append(el("div", { class: "muted small" }, "this browser does not support Web Push"));
      return;
    }
    const info = await api("/api/push").catch(() => null);
    const head = el("div", { class: "t-title" },
      `Web Push (works with the browser closed) — this browser: ${st.subscribed ? "subscribed" : "not subscribed"}`
      + (info ? ` · ${info.subscriptions} browser(s) total` : ""));
    const sub = el("button", { class: "btn small primary", hidden: st.subscribed || null }, "enable on this browser");
    const unsub = el("button", { class: "btn small danger", hidden: st.subscribed ? null : "" }, "disable on this browser");
    const test = el("button", { class: "btn small", hidden: st.subscribed ? null : "" }, "send test");
    sub.onclick = async () => {
      sub.disabled = true;
      try { await notify.pushSubscribe(); toast("subscribed — decisions push to this browser now"); renderPush(); }
      catch (err) { toast(err.message, 5000, { error: true }); sub.disabled = false; }
    };
    unsub.onclick = async () => {
      unsub.disabled = true;
      try { await notify.pushUnsubscribe(); toast("push disabled on this browser"); renderPush(); }
      catch (err) { toast(err.message, 5000, { error: true }); unsub.disabled = false; }
    };
    test.onclick = async () => {
      try { const r = await api("/api/push/test", { method: "POST" });
        toast(`test sent to ${r.sent} browser(s)`); }
      catch (err) { toast(err.message, 5000, { error: true }); }
    };
    pushRow.append(head,
      el("div", { class: "muted small" },
        "one notification per new decision, sent by the server — subscribe each browser/device you want reached"),
      el("div", { class: "row mt" }, sub, unsub, test));
  };
  renderPush();
  return panel;
}
