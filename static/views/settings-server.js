// Settings -> server process (runtime config knobs + graceful restart) - split from settings.js (one section per module; settings.js keeps
// the section order, nav, and deep-link jump). Appends its own panel and returns the fill
// promise so settings.js can await all sections before the anchor jump.

import { api } from "/static/api.js";
import { act, el, toast, toastError, when } from "/static/util.js";
import { panelSection } from "/static/views/settings-common.js";

export function renderServerConfig(view) {
  // -- server process: runtime config knobs, then a graceful restart onto committed code ----
  return panelSection(view, "/api/settings/server", ["50%", "70%"], (srvCfgBox, c, reload) => {
    const sandboxSel = el("select", {}, ["strict", "permissive", "off"].map((m) => el("option", {}, m)));
    sandboxSel.value = c.sandbox || "permissive";
    const concIn = el("input", { type: "number", min: "1", value: String(c.max_concurrent_runs ?? 2), style: "width:90px" });
    const rescanIn = el("input", { type: "number", min: "1", value: String(c.registry_rescan_s ?? 30), style: "width:90px" });
    const ghIn = el("input", { type: "text", value: c.github_client_id || "",
      placeholder: "default: the gh CLI's client id", style: "width:100%;max-width:420px" });
    // The noVNC page for the shared browser the routines drive. Set it and the console grows
    // a Browser section plus a live preview in the right rail; leave it empty and neither
    // appears — an instance that never published the port should not show a dead link.
    const vncIn = el("input", { type: "text", value: c.browser_view_url || "",
      placeholder: "http://host:6080/vnc.html", style: "width:100%;max-width:420px" });
    // The agent desktops: the broker the console asks for the fleet, and the noVNC page every
    // desktop's screen is relayed from. Both, or the console shows no Desktops section at all.
    const deskBrokerIn = el("input", { type: "text", value: c.desktop_broker_url || "",
      placeholder: "http://172.30.7.20:8790", style: "width:100%;max-width:420px" });
    const deskVncIn = el("input", { type: "text", value: c.desktop_view_url || "",
      placeholder: "http://172.30.7.20:6080/vnc.html", style: "width:100%;max-width:420px" });
    const save = el("button", { class: "btn small primary" }, "save server settings");
    save.onclick = () => act(save, async () => {
      const r = await api("/api/settings/server", { method: "PUT", body: {
        sandbox: sandboxSel.value, max_concurrent_runs: Number(concIn.value),
        registry_rescan_s: Number(rescanIn.value), github_client_id: ghIn.value.trim(),
        browser_view_url: vncIn.value.trim(), desktop_broker_url: deskBrokerIn.value.trim(),
        desktop_view_url: deskVncIn.value.trim() } });
      toast(r.restart_for?.length ? "server settings saved — restart to resize concurrency" : "server settings saved");
    });
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
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "browser screen (noVNC) URL"), vncIn)),
      el("div", { class: "faint small", style: "margin-top:6px" },
        "browser screen: the noVNC page showing the shared signed-in browser the routines ",
        "drive. Set it and a Browser section appears in the nav with a live preview in the ",
        "right rail; leave it empty and neither is shown."),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "desktop broker URL"), deskBrokerIn),
        el("label", { class: "field" }, el("span", {}, "desktop screen (noVNC) URL"), deskVncIn)),
      el("div", { class: "faint small", style: "margin-top:6px" },
        "agent desktops: the broker that runs one desktop per routine, and the noVNC page their ",
        "screens are served from. Set both and a Desktops section appears in the nav, with a ",
        "preview of the busiest desktop beside the browser's; the screens open with the ",
        "DESKTOP_OPERATOR_TOKEN secret and the fleet with DESKTOP_VM_TOKEN as well."),
      el("div", { class: "row mt" }, save),
      el("div", { class: "faint small", style: "margin-top:6px" },
        "sandbox: strict = refuse to run a util unsandboxed · permissive = jail when the kernel ",
        "allows, warn and run bare otherwise · off = never jail"));
  });
}

export function renderServer(view) {
  return panelSection(view, "/api/status", ["50%", "80%"], (srvBox, s, reload) => {
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
    // It stops with the page, as the GitHub and OAuth flows' polls do: it is armed on RENDER
    // whenever a restart is pending, so without the check every visit to Settings during the
    // quiet-gap wait left one more 2 s /api/status poll running for three minutes.
    async function watch(initialStarted) {
      btn.disabled = true;
      withdraw.hidden = false;
      const t0 = Date.now();
      while (Date.now() - t0 < 180000) {
        await new Promise((r) => setTimeout(r, 2000));
        if (!srvBox.isConnected) return;
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
          reload();
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
      btn.textContent = "↻ restart server"; btn.classList.remove("danger", "armed");
      statusLine.textContent = "";
    };
    cancel.onclick = disarm;
    btn.onclick = async () => {
      if (!armed) {   // two-step confirm, in place
        armed = true; cancel.hidden = false;
        btn.textContent = "confirm restart"; btn.classList.add("danger", "armed");
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
      } catch (err) { toastError(err, 5000); }
    };
    withdraw.onclick = () => act(withdraw, () => api("/api/settings/restart", { method: "DELETE" }),
                                 "restart request withdrawn");

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
  });
}
