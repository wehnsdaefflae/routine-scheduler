// Settings view - the section ORDER, nav, deep-link jump, and first-run banner. Every
// section body lives in its own settings-*.js module (renderX(view, ...) appends its
// panel and returns the fill promise); this file only sequences them.

import { api } from "/static/api.js";
import { setQuery } from "/static/router.js";
import { el, toast } from "/static/util.js";
import { renderConnections } from "/static/views/settings-connections.js";
import { renderEndpoints } from "/static/views/settings-endpoints.js";
import { renderGithub } from "/static/views/settings-github.js";
import { renderLibraries, renderLibrarySync } from "/static/views/settings-library.js";
import { renderMachines } from "/static/views/settings-machines.js";
import { renderNotifications } from "/static/views/settings-notify.js";
import { renderSecrets } from "/static/views/settings-secrets.js";
import { renderServer, renderServerConfig } from "/static/views/settings-server.js";
import { renderSource } from "/static/views/settings-source.js";

export async function render(view, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / configuration"),
      el("h1", {}, "Settings"))));

  // Section nav - a visible location indicator within Settings; the active sub-section is in the
  // URL (#/settings?section=endpoints), so a deep link / reload lands on the same section.
  // Endpoints (with the model catalog + system model) leads: it is the first-run critical path.
  const SECTIONS = [["endpoints", "Endpoints"], ["github", "GitHub"],
                    ["connections", "Connections"], ["machines", "Machines"],
                    ["secrets", "Secrets"],
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

  // -- first-run setup banner -----------------------------------------------------
  const st = await api("/api/status").catch(() => ({}));
  if (st.needs_setup) {
    const banner = el("div", { class: "panel warn", style: "margin-bottom:14px" });
    const done = el("button", { class: "btn small primary" }, "finish setup");
    done.onclick = async () => {
      try { await api("/api/setup/complete", { method: "POST" }); toast("setup complete - no more first-run redirect"); banner.remove(); }
      catch (err) { toast(err.message, 5000, { error: true }); }
    };
    banner.append(
      el("strong", {}, "First-run setup"),
      el("div", { class: "muted mt small" },
        "Add a model provider (LLM endpoints, at the top), connect GitHub, and point at your repos - ",
        "Test each remote. When you're set:"),
      el("div", { class: "row mt" }, done));
    view.append(banner);
  }

  // -- sections, in DOM order: each module appends its containers synchronously, so calling
  // it un-awaited keeps the order while its data loads in parallel with the sections below.
  view.append(sectionHead("endpoints", "LLM endpoints"));
  const endpointsReady = renderEndpoints(view);
  view.append(sectionHead("github", "GitHub"));
  const githubReady = renderGithub(view, query);
  view.append(sectionHead("connections", "Connections"));
  const connectionsReady = renderConnections(view);
  view.append(sectionHead("machines", "Machines"));
  const machinesReady = renderMachines(view);
  view.append(sectionHead("secrets", "Secrets"));
  const secretsReady = renderSecrets(view);
  view.append(sectionHead("libraries", "Library repository"));
  const librariesReady = renderLibraries(view);
  view.append(sectionHead("library-sync", "Library sync"));
  const librarySyncReady = renderLibrarySync(view, st.server_tz || "");
  view.append(sectionHead("source", "Source repository"));
  const sourceReady = renderSource(view);
  view.append(sectionHead("server", "Server"));
  const serverCfgReady = renderServerConfig(view);
  const serverReady = renderServer(view);
  view.append(sectionHead("notifications", "Notifications"));
  view.append(renderNotifications());

  // The section fills load in parallel - every panel was appended above in its final DOM
  // order, so each async render only fills its own box. Wait for all of them before the
  // deep-link jump so the anchor lands on settled heights.
  await Promise.all([endpointsReady, githubReady, connectionsReady, machinesReady, secretsReady,
                     librariesReady, librarySyncReady, sourceReady, serverCfgReady, serverReady]);

  // Land on the requested section (deep link / reload). Everything above is now in the DOM, so
  // the anchor exists; jump without smooth-scroll on first paint.
  if (query.section) goSection(query.section, false);
}
