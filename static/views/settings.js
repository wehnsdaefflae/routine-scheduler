// Settings view - grouped by WHAT you're configuring, so the page teaches a cognitive model:
// Intelligence (models) · Connections (accounts + machines) · Code & library (the repos) ·
// This instance (secrets, server, notifications). Each section keeps its stable id="sec-<id>"
// (deep links #/settings?section=<id> AND the side-TOC depend on it); every section BODY lives
// in its own settings-*.js module (renderX(view, ...) appends its panel and returns the fill
// promise). This file owns only order, grouping, nav, the per-section copy, and the banner.

import { api } from "/static/api.js";
import { setQuery } from "/static/router.js";
import { settingsSection } from "/static/components/settings-section.js";
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

  const st = await api("/api/status").catch(() => ({}));

  // The cognitive model: four groups answering "what am I configuring?". A section's `desc` is a
  // reader-side one-liner (what it controls); a group's `blurb` says why those sections belong
  // together. `nav` is the short chip label; `title` is the section heading (and TOC text).
  // Intelligence leads because LLM endpoints are the first-run critical path.
  const GROUPS = [
    { label: "Intelligence",
      blurb: "Where your routines get their reasoning — the providers they call and the models they pick from.",
      sections: [
        { id: "endpoints", nav: "Endpoints", title: "LLM endpoints",
          desc: "Provider connections, the model catalog, and the system model for the scheduler's own helper calls.",
          fill: (v) => renderEndpoints(v) },
      ] },
    { label: "Connections",
      blurb: "External accounts and machines the scheduler signs into on your behalf.",
      sections: [
        { id: "github", nav: "GitHub", title: "GitHub",
          desc: "The GitHub account used to clone and push your routine and library repositories.",
          fill: (v) => renderGithub(v, query) },
        { id: "connections", nav: "Connections", title: "Connections",
          desc: "OAuth logins — Google, Notion and more — that routines act through, bound per routine.",
          fill: (v) => renderConnections(v) },
        { id: "machines", nav: "Machines", title: "Machines",
          desc: "Remote hosts routines can reach over SSH for work that must run on another machine.",
          fill: (v) => renderMachines(v) },
      ] },
    { label: "Code & library",
      blurb: "The Git repositories that define the scheduler itself and its shared workflow library.",
      sections: [
        { id: "source", nav: "Source", title: "Source repository",
          desc: "The scheduler's own code — the fork the self-audit routine commits and pushes its changes to.",
          fill: (v) => renderSource(v) },
        { id: "libraries", nav: "Library", title: "Library repository",
          desc: "The shared workflow patterns, practice modules and permissions every routine draws on.",
          fill: (v) => renderLibraries(v) },
        { id: "library-sync", nav: "Library sync", title: "Library sync",
          desc: "When the library repository is pulled and pushed automatically.",
          fill: (v) => renderLibrarySync(v, st.server_tz || "") },
      ] },
    { label: "This instance",
      blurb: "The secret store, this server process, and how the console reaches you.",
      sections: [
        { id: "secrets", nav: "Secrets", title: "Secrets",
          desc: "The central credential store — keys and passwords injected only into utils that declare them.",
          fill: (v) => renderSecrets(v) },
        { id: "server", nav: "Server", title: "Server",
          desc: "Runtime configuration for this process, plus a graceful restart.",
          fill: (v) => Promise.all([renderServerConfig(v), renderServer(v)].filter(Boolean)) },
        { id: "notifications", nav: "Notifications", title: "Notifications",
          desc: "Browser and push alerts for decisions waiting on you.",
          fill: (v) => { v.append(renderNotifications()); } },
      ] },
  ];

  // Section nav - a visible location indicator, grouped to mirror the page. The active section is
  // in the URL (#/settings?section=endpoints), so a deep link / reload lands on the same section.
  const secNav = el("div", { class: "filterbar settings-nav" });
  view.append(secNav);
  const goSection = (id, smooth = true) => {
    setQuery({ section: id });
    document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
    secNav.querySelectorAll(".tag").forEach((b) => b.classList.toggle("on", b.dataset.sec === id));
  };
  for (const g of GROUPS) {
    secNav.append(el("span", { class: "lbl" }, g.label));
    for (const s of g.sections) {
      const b = el("span", { class: "tag click", onclick: () => goSection(s.id) }, s.nav);
      b.dataset.sec = s.id;
      secNav.append(b);
    }
  }

  // -- first-run setup banner -----------------------------------------------------
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

  // -- groups, in DOM order: each group prints its eyebrow + blurb, then its sections. Every
  // section appends its <h2 id="sec-…"> and a description, then its module fills the panel below
  // (called un-awaited so all the panel data loads in parallel while DOM order stays fixed).
  const fills = [];
  GROUPS.forEach((g, gi) => {
    view.append(el("div", { class: "set-group", style: gi ? "margin-top:26px" : "" },
      el("div", { class: "kicker" }, g.label),
      el("div", { class: "set-groupblurb muted small" }, g.blurb)));
    for (const s of g.sections) {
      view.append(...settingsSection({ title: s.title, id: s.id }, s.desc));
      const p = s.fill(view);
      if (p) fills.push(p);
    }
  });

  // The section fills load in parallel - every panel was appended above in its final DOM order,
  // so each async render only fills its own box. Wait for all before the deep-link jump so the
  // anchor lands on settled heights.
  await Promise.all(fills);

  // Land on the requested section (deep link / reload); otherwise just highlight the first chip.
  if (query.section) goSection(query.section, false);
  else secNav.querySelector(".tag")?.classList.add("on");
}
