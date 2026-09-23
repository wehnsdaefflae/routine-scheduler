// Help: the repository's hand-written guides (docs/*.md) plus the API reference generated from
// the source's own docstrings by pdoc — both rebuilt at daemon boot whenever the source changed
// (docs_build.py) and served as static, self-contained pages under /docs. A page is embedded in
// an iframe: each is a complete HTML document, so the SPA's no-HTML-injection invariant holds.
//
// The list is an INDEX COLUMN, not a chip row. There are three dozen guides; as chips they
// wrapped into a five-line wall above the reading frame, with no order a reader could see and
// the frame pushed off the first screen. The column keeps the server's reading order
// (docs_build.GUIDE_ORDER — orientation, then worked examples, then the contract docs, then
// everything else alphabetically), so the order is defined in ONE place and this view does not
// carry a second copy of it. The API reference is the last entry, under a rule: it is generated,
// not written, and a reader looking for prose should not meet it first.

import { navigate } from "/static/router.js";
import { el } from "/static/util.js";

export async function render(view, sub, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("h1", {}, "Help"))));

  let index = null;
  let fetchError = "";   // a network/server failure is NOT "still being generated"
  try {
    const resp = await fetch("/docs/index.json");
    if (resp.ok) index = await resp.json();
    else if (resp.status !== 404) fetchError = `the server answered ${resp.status}`;
  } catch (err) { fetchError = err.message || "the request failed"; }
  if (!index || !Array.isArray(index.guides)) {
    view.append(el("div", { class: "empty" },
      el("div", { class: "t" }, fetchError
        ? "documentation could not be loaded" : "documentation is still being generated"),
      el("div", { class: "d" }, fetchError
        ? `${fetchError} — check the daemon log, then reload`
        : "the daemon builds it in the background at boot (a few seconds) — reload shortly")));
    return;
  }

  // NO GUIDES is not the same as no documentation, and it is not a state to pass over in
  // silence: the page then shows one entry ("API reference") over pdoc's module index —
  // `assists, bootstrap, branches, captured_output…` — to a reader who clicked Help. The
  // guides are the repository's own docs/*.md, read from beside the source the daemon runs, so
  // an empty set is a SOURCE-ROOT problem and the page says where to look at it.
  if (!index.guides.length) {
    view.append(el("div", { class: "panel warn", "data-no-guides": "" },
      el("strong", {}, "No written guides are published"),
      el("div", { class: "set-desc muted small mt" },
        "The daemon builds them from the ", el("code", {}, "docs/"), " directory beside the "
        + "source it is running — and found none, so the only page below is the generated API "
        + "reference. Check the source root in ",
        el("a", { href: "#/settings?section=source" }, "Settings → Source"),
        ": it must be the repository itself, not its ", el("code", {}, "src/"), " directory.")));
  }

  const pages = [...index.guides.map((g) => [g.slug, g.title]), ["api", "API reference"]];
  const current = pages.some(([slug]) => slug === sub) ? sub : pages[0][0];

  // A REAL href, so a guide opens in a new tab like any other link; the router turns a plain
  // click into a re-render of this view with the new sub-path.
  const entry = ([slug, title]) => el("a", {
    class: `help-entry${slug === current ? " on" : ""}`, href: `#/help/${slug}`,
    title, onclick: (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
      e.preventDefault();
      navigate(`#/help/${slug}`);
    } }, title);
  const guides = pages.slice(0, -1);
  view.append(el("div", { class: "help-layout" },
    el("nav", { class: "help-index" },
      guides.length ? el("div", { class: "help-index-cap" }, "guides") : null,
      ...guides.map(entry),
      el("div", { class: "help-index-cap gen" }, "generated"),
      entry(pages[pages.length - 1])),
    el("div", { class: "help-main" },
      // The generated page is read INSIDE the console's frame, so it takes the CONSOLE's theme
      // and not the machine's: `?theme=` stamps `data-theme` in the guide shell exactly as
      // index.html's pre-paint script does out here (docs_build.THEME_SCRIPT). Without it a
      // reader who chose light by hand got a dark rectangle inside a light shell, on the one
      // page whose job is explaining the console. The API reference is pdoc's own document and
      // reads no parameter yet; it follows the machine through the same tokens either way.
      el("iframe", { class: "help-frame", title: "documentation",
        src: (current === "api" ? `/docs/${index.api}` : `/docs/guides/${current}.html`)
           + `?theme=${document.documentElement.dataset.theme || "auto"}` }),
      el("div", { class: "faint small", style: "margin-top:6px" },
        `generated from the running source (v${index.version}) — ${guides.length} guide`
        + `${guides.length === 1 ? "" : "s"} from docs/*.md, the API reference from docstrings `
        + "via pdoc"))));
}
