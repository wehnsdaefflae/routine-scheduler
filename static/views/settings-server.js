// Settings -> server process (runtime config knobs + graceful restart) - split from settings.js (one section per module; settings.js keeps
// the section order, nav, and deep-link jump). Appends its own panel and returns the fill
// promise so settings.js can await all sections before the anchor jump.

import { api } from "/static/api.js";
import { el, skeleton, toast, when } from "/static/util.js";

export function renderServerConfig(view) {
  // -- server process: runtime config knobs, then a graceful restart onto committed code ----
  const srvCfgBox = el("div", { class: "panel" });
  srvCfgBox.append(skeleton(["50%", "70%"]));
  view.append(srvCfgBox);
  async function fill() {
    let c;
    try { c = await api("/api/settings/server"); }
    catch (err) { srvCfgBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    const sandboxSel = el("select", {}, ["strict", "permissive", "off"].map((m) => el("option", {}, m)));
    sandboxSel.value = c.sandbox || "permissive";
    const concIn = el("input", { type: "number", min: "1", value: String(c.max_concurrent_runs ?? 2), style: "width:90px" });
    const rescanIn = el("input", { type: "number", min: "1", value: String(c.registry_rescan_s ?? 30), style: "width:90px" });
    const ghIn = el("input", { type: "text", value: c.github_client_id || "",
      placeholder: "default: the gh CLI's client id", style: "width:100%;max-width:420px" });
    const save = el("button", { class: "btn small primary" }, "save server settings");
    save.onclick = async () => {
      try {
        const r = await api("/api/settings/server", { method: "PUT", body: {
          sandbox: sandboxSel.value, max_concurrent_runs: Number(concIn.value),
          registry_rescan_s: Number(rescanIn.value), github_client_id: ghIn.value.trim() } });
        toast(r.restart_for?.length ? "server settings saved — restart to resize concurrency" : "server settings saved");
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    srvCfgBox.replaceChildren(
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "Runtime knobs in config.yaml. The sandbox mode applies to the next util call and the ",
        "rescan cadence to the next scan; max concurrent runs sizes the run pool at startup, so it ",
        "needs a restart (below). Homes, bind, port, and the auth token stay install-time."),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "util sandbox"), sandboxSel),
        el("label", { class: "field" }, el("span", {}, "max concurrent runs"), concIn),
        el("label", { class: "field" }, el("span", {}, "registry rescan (s)"), rescanIn)),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "github OAuth client id"), ghIn)),
      el("div", { class: "row mt" }, save),
      el("div", { class: "faint small", style: "margin-top:6px" },
        "sandbox: strict = refuse to run a util unsandboxed · permissive = jail when the kernel ",
        "allows, warn and run bare otherwise · off = never jail"));
  }
  return fill();
}

export function renderServer(view) {
  const srvBox = el("div", { class: "panel" });
  srvBox.append(skeleton(["50%", "80%"]));
  view.append(srvBox);
  async function fill() {
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
          fill();
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
  return fill();
}
