// Help: hand-written guides (docs/*.md) + the API reference generated from the source's
// own docstrings by pdoc — rebuilt at daemon boot whenever the source changed (docs_build.py)
// and served as static, self-contained pages under /docs. Pages embed via an iframe: they
// are complete HTML documents, and the SPA's no-HTML-injection invariant stays intact.

import { navigate } from "/static/router.js";
import { el } from "/static/util.js";

export async function render(view, sub, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / documentation"),
      el("h1", {}, "Help"))));

  let index = null;
  try {
    const resp = await fetch("/docs/index.json");
    if (resp.ok) index = await resp.json();
  } catch { /* treated as not-built below */ }
  if (!index || !Array.isArray(index.guides)) {
    view.append(el("div", { class: "empty" },
      el("div", { class: "t" }, "documentation is still being generated"),
      el("div", { class: "d" },
        "the daemon builds it in the background at boot (a few seconds) — reload shortly")));
    return;
  }

  // One chip per guide + the API reference; the open page lives in the URL (#/help/<slug>).
  const pages = [...index.guides.map((g) => [g.slug, g.title]), ["api", "API reference"]];
  const current = pages.some(([slug]) => slug === sub) ? sub : pages[0][0];
  view.append(el("div", { class: "filterbar" },
    el("span", { class: "lbl" }, "page"),
    pages.map(([slug, title]) =>
      el("span", { class: `tag click${slug === current ? " on" : ""}`,
        onclick: () => navigate(`#/help/${slug}`) }, title))));

  const src = current === "api" ? `/docs/${index.api}` : `/docs/guides/${current}.html`;
  view.append(el("iframe", { class: "help-frame", src, title: "documentation" }));
  view.append(el("div", { class: "faint small", style: "margin-top:6px" },
    `generated from the running source (v${index.version}) — API reference from docstrings ` +
    "via pdoc, guides from docs/*.md"));
}
