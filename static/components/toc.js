// Sticky side table-of-contents. On wide viewports it parks a fixed rail in the right margin
// (like the run/conversation rails — same 1560px breakpoint) listing the page's <h2> sections,
// with click-to-scroll and the in-view section highlighted. Purely additive: no layout change,
// hidden below 1560px, and skipped on views that already carry their own rail/nav.

import { el } from "/static/util.js";

export function mountToc(box) {
  // Views with their own navigation column don't get a second one.
  if (box.querySelector(".run-rail, .recipe-navcol")) return null;
  const heads = [...box.querySelectorAll("h2")].filter((h) => (h.textContent || "").trim());
  if (heads.length < 2) return null;   // nothing worth a TOC

  const links = heads.map((h, i) => {
    if (!h.id) {
      const base = h.textContent.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "") || "section";
      h.id = `toc-${base}-${i}`;
    }
    h.style.scrollMarginTop = "84px";   // land clear of the sticky topbar
    const a = el("a", { class: "toc-link", title: h.textContent.trim(),
      onclick: (e) => { e.preventDefault(); h.scrollIntoView({ behavior: "smooth", block: "start" }); } },
      h.textContent.trim());
    a.dataset.tocFor = h.id;
    return a;
  });

  const nav = el("nav", { class: "side-toc", "aria-label": "on this page" },
    el("div", { class: "side-toc-cap" }, "On this page"), ...links);
  document.body.append(nav);

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
