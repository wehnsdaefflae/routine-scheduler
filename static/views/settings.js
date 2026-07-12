// Settings: GitHub device flow, secrets store, library repos, source repo, server restart.
// The LLM endpoints section (CRUD + system model + live test) lives in settings-endpoints.js.

import { api } from "/static/api.js";
import { setQuery } from "/static/router.js";
import { el, skeleton, toast, when } from "/static/util.js";
import { renderEndpoints } from "/static/views/settings-endpoints.js";

export async function render(view, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / configuration"),
      el("h1", {}, "Settings"))));

  // Section nav — a visible location indicator within Settings; the active sub-section is in the
  // URL (#/settings?section=endpoints), so a deep link / reload lands on the same section.
  const SECTIONS = [["github", "GitHub"], ["secrets", "Secrets"], ["libraries", "Library"],
                    ["source", "Source"], ["server", "Server"], ["endpoints", "Endpoints"]];
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
        "Add a model provider (LLM endpoints, below), connect GitHub, and point at your repos — ",
        "Test each remote. When you're set:"),
      el("div", { class: "row mt" }, done));
    view.append(banner);
  }

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
  await renderGithub();

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
        if (!confirm(`Delete secret ${k}?`)) return;
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
          return el("tr", {},
            el("td", {}, n.key),
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
    secBox.append(el("div", { class: "row mt" }, keyIn, valIn, save));
  }
  await renderSecrets();

  // -- the library repository ------------------------------------------------------
  view.append(sectionHead("libraries", "Library repository"));
  const libBox = el("div", { class: "panel" });
  libBox.append(skeleton(["60%", "90%"]));
  view.append(libBox);
  try {
    const { libraries } = await api("/api/settings/libraries");
    libBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
      "One git repo holds everything the instance acquires: workflows/, traits/, permissions/, utils/ ",
      "(with the gu dispatcher) — plus routines/ and sanitized config, exported by library-sync. ",
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

  // -- scheduler source repository (self-audit's push target) ---------------------
  view.append(sectionHead("source", "Source repository"));
  const srcBox = el("div", { class: "panel" });
  srcBox.append(skeleton(["60%", "90%"]));
  view.append(srcBox);
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
  await renderServer();

  // -- LLM endpoints (settings-endpoints.js) ---------------------------------------
  view.append(sectionHead("endpoints", "LLM endpoints"));
  await renderEndpoints(view);

  // Land on the requested section (deep link / reload). Everything above is now in the DOM, so
  // the anchor exists; jump without smooth-scroll on first paint.
  if (query.section) goSection(query.section, false);
}
