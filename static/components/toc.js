// "On this page": the page's <h2> sections, click to scroll, the section you are in lit.
//
// It lives IN THE NAVIGATION RAIL, under the destinations and above the rail's foot — the one
// place with room at every desktop width. It used to park in the right margin like the
// run/conversation rails, which meant a 1900px viewport: a 1440px laptop got no wayfinding at
// all on pages between eleven and twenty-six THOUSAND pixels tall (stats 26 428px, messages
// 18 117px, a routine 13 732px), while 430px of the rail below "Help" sat empty on every one of
// them. The rail already scrolls and already ends in a fixed foot, so the index takes the slack
// between the two and scrolls inside it.
//
// Below 1181px the rail is a 68px icon strip and below 861px a bottom bar — no room for words
// either way, so base.css hides it there and the page is read top-to-bottom. Skipped on views
// that carry their own page-level rail.

import { el } from "/static/util.js";

export function mountToc(box) {
  // Views with their own page-level rail don't get a second one. The routine page's
  // .recipe-navcol is a WITHIN-section file tree, not a page rail, so it doesn't disqualify
  // the page from a sections TOC (the routine page is long and wants one, like Settings).
  if (box.querySelector(".run-rail")) return null;
  const heads = [...box.querySelectorAll("h2")].filter((h) => (h.textContent || "").trim());
  if (heads.length < 2) return null;   // nothing worth a TOC

  const links = heads.map((h, i) => {
    if (!h.id) {
      const base = h.textContent.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "") || "section";
      h.id = `toc-${base}-${i}`;
    }
    const a = el("a", { class: "toc-link", title: h.textContent.trim(),
      onclick: (e) => { e.preventDefault(); h.scrollIntoView({ behavior: "smooth", block: "start" }); } },
      h.textContent.trim());
    a.dataset.tocFor = h.id;
    return a;
  });

  const nav = el("nav", { class: "side-toc", "aria-label": "on this page" },
    el("div", { class: "side-toc-cap" }, "On this page"), ...links);
  // Between the destinations and the rail's foot. app.js calls this inside a try — a console
  // whose rail is missing has bigger problems than a missing index.
  const rail = document.querySelector(".topbar");
  rail.insertBefore(nav, rail.querySelector(".rail-foot"));

  // Highlight whichever section is currently in view.
  const byId = new Map(links.map((a) => [a.dataset.tocFor, a]));
  links[0]?.classList.add("on");
  const obs = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (!en.isIntersecting) continue;
      links.forEach((a) => a.classList.remove("on"));
      byId.get(en.target.id)?.classList.add("on");
    }
  }, { rootMargin: "-80px 0px -70% 0px", threshold: 0 });
  heads.forEach((h) => obs.observe(h));

  return () => { obs.disconnect(); nav.remove(); };
}
