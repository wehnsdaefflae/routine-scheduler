// Workflow library: list with lint badges, editor with git history, fragments, proposals.

import { api } from "/static/api.js";
import { chip, el, toast } from "/static/util.js";

export async function render(view) {
  let data;
  try { data = await api("/api/workflows"); }
  catch (err) { view.append(el("div", { class: "empty" }, err.message)); return; }

  view.append(el("div", { class: "row spread" },
    el("h1", {}, "Workflow library"),
    el("span", { class: "muted mono" }, `@ ${data.head || "no git"}`)));

  const detail = el("div", {});

  view.append(el("h2", {}, "Workflows"));
  const wfPanel = el("div", { class: "panel", style: "padding:0" });
  wfPanel.append(el("table", { class: "list" },
    el("tbody", {}, data.workflows.map((w) => el("tr", {},
      el("td", {}, el("a", { href: "#", onclick: (e) => { e.preventDefault(); open(w.slug, false); } },
        w.name || w.slug)),
      el("td", {}, chip(w.status, w.status === "stable" ? "ok" : "partial")),
      el("td", {}, `v${w.version}`),
      el("td", {}, w.problems.length ? chip(`${w.problems.length} lint`, "failed") : chip("lint ok", "ok")),
      el("td", { class: "muted", style: "max-width:420px" }, w.description))))));
  view.append(wfPanel);

  view.append(el("h2", {}, "Fragments"));
  view.append(el("div", { class: "panel" },
    el("div", { class: "row" }, data.fragments.map((f) =>
      el("button", { class: "btn small", onclick: () => open(f.slug, true) },
        `${f.slug}${f.problems.length ? " ⚠" : ""}`)))));

  view.append(detail);

  async function open(slug, isFragment) {
    const d = await api(`/api/workflows/${slug}${isFragment ? "?fragment=true" : ""}`);
    detail.innerHTML = "";
    const ta = el("textarea", { class: "code", style: "min-height:340px" }, d.content);
    const save = el("button", { class: "btn primary" }, "save (lint-gated) + commit");
    save.onclick = async () => {
      try {
        await api(`/api/workflows/${slug}`, { method: "PUT",
          body: { content: ta.value, fragment: isFragment } });
        toast("saved + committed");
      } catch (err) { toast(err.message, 5000); }
    };
    detail.append(el("h2", {}, `${isFragment ? "fragment" : "workflow"}: ${slug}`),
      el("div", { class: "panel" }, ta,
        el("div", { class: "row mt" }, save),
        el("details", { class: "mt" },
          el("summary", { style: "cursor:pointer" }, "git history"),
          el("table", { class: "list" }, el("tbody", {}, (d.log || []).map((c) =>
            el("tr", {}, el("td", { class: "mono" }, c.commit), el("td", {}, c.date),
              el("td", { class: "muted" }, c.subject))))))));
    detail.scrollIntoView({ behavior: "smooth" });
  }

  // proposals
  const proposals = await api("/api/proposals").catch(() => []);
  view.append(el("h2", {}, "Proposals (from the meta routine)"));
  if (!proposals.length) {
    view.append(el("div", { class: "panel muted" }, "none open"));
  } else {
    for (const p of proposals) {
      const note = el("input", { type: "text", placeholder: "optional note…", style: "flex:1" });
      const decideBtn = (decision) => el("button",
        { class: `btn small ${decision === "accepted" ? "primary" : "danger"}`,
          onclick: async () => {
            try {
              await api(`/api/proposals/${p.id}/decide`, { method: "POST",
                body: { decision, note: note.value } });
              toast(`proposal ${decision}`);
              location.reload();
            } catch (err) { toast(err.message); }
          } }, decision);
      view.append(el("div", { class: "panel mt" },
        el("div", { class: "row spread" },
          el("strong", {}, p.id),
          p.decision ? chip(p.decision.decision, p.decision.decision === "accepted" ? "ok" : "failed") : null),
        el("pre", { class: "doc mt" }, p.content),
        p.decision ? null : el("div", { class: "row mt" }, note, decideBtn("accepted"), decideBtn("declined"))));
    }
  }
}
