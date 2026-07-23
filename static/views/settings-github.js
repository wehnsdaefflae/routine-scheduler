// Settings -> GitHub device-flow connection - split from settings.js (one section per module; settings.js keeps
// the section order, nav, and deep-link jump). Appends its own panel and returns the fill
// promise so settings.js can await all sections before the anchor jump.

import { api } from "/static/api.js";
import { setQuery } from "/static/router.js";
import { el, skeleton, toast } from "/static/util.js";

export function renderGithub(view, query) {
  // -- GitHub connection (device flow — no container terminal) ---------------------
  const ghBox = el("div", { class: "panel" });
  ghBox.append(skeleton(["50%", "80%"]));
  view.append(ghBox);
  async function fill() {
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
        if (p.status === "connected") { toast(`GitHub connected as ${p.login}`); connect.disabled = false; setQuery({ flow: "" }); fill(); return; }
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
  return fill();
}
