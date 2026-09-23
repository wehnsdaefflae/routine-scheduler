// Routine config — IDENTITY sections: name, description, tags, the settings TEMPLATE it
// started from, the DOMAIN it belongs to, and its origin pattern.
//
// Split out of routine-config.js, which had grown to twenty panels in one closure. The cut
// follows routine.js's own SECTION_GROUPS, so a module here is exactly what one labelled group
// on the page shows — "Identity & origin", plus the two provenance controls that answer the
// same question from the other side: a TEMPLATE copies once and the copy becomes this
// routine's own, a DOMAIN layers under this routine's file at every load.
//
// Every section is built by the shared settingsSection primitive in its { title, id } form, so
// each heading carries a stable `sec-<id>` anchor — the address a fix link elsewhere on the
// page aims at. Ids are a contract: reword a heading freely, keep its id.

import { api } from "/static/api.js";
import { remount } from "/static/router.js";
import { act, el, toast, toastError } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { tagsEditor } from "/static/components/tags.js";
import { templatePanel } from "/static/components/template-panel.js";

/** The DOMAIN picker (docs/lanes-domains.md): which shared surface this routine is part of —
 *  at most one, named in this routine's OWN routine.yaml, so joining and leaving are ordinary
 *  config saves through the same PATCH as everything else on this page. The detail payload
 *  carries the stored id as `domain` ("" for none) and the PATCH takes it back the same way,
 *  which is why at-most-one needs no rule: the file has one field.
 *
 *  The LANE is deliberately NOT here. It decides the ORDER several routines fire in, belongs to
 *  no single one of them, and is edited on the Routines page (the hero reports which lane this
 *  routine is in). Keeping the two apart is what stops a TIMING decision from changing this
 *  routine's permissions and its shared store as a side effect, unannounced on either page.
 *
 *  A save REMOUNTS the view: every panel below shows the EFFECTIVE config, produced by a merge
 *  the server does when it loads the routine — so a stale page would keep showing the surface
 *  of the domain just left. A remount re-runs this view alone; a page reload would also drop
 *  the SSE bus, the LLM dock and the browser dock, and re-run the whole boot sequence. */
function domainSection(view, slug, d) {
  const stored = d.domain || "";
  const sel = el("select", { "data-domain-sel": "", disabled: true },
    el("option", { value: "" }, "loading…"));
  const detail = el("div", { class: "muted small mt", "data-domain-detail": "" });
  let domains = [];
  const describe = () => {
    const id = sel.value;
    if (!id) {
      detail.replaceChildren("in no domain — every setting on this page is this routine's own");
      return;
    }
    const chosen = domains.find((x) => x.id === id);
    if (!chosen) {
      // routine.yaml names a domain the store no longer holds: nothing is merged and no store
      // is mounted, which a picker quietly reading "none" would hide behind a plausible answer.
      detail.replaceChildren("this file names ", el("code", {}, id),
        ", which is not in the store — nothing is inherited and no shared store is mounted. "
        + "Pick a domain that exists, or none.");
      return;
    }
    const others = (chosen.members || []).filter((m) => m !== slug);
    detail.replaceChildren(
      el("div", {}, "shared store · ", el("code", {}, chosen.store || "—")),
      el("div", { style: "margin-top:4px" }, others.length
        ? `shared with · ${others.join(" · ")}`
        : "no other routine is in it yet"));
  };
  (async () => {
    try {
      const data = await api("/api/domains");
      domains = data.domains || [];
      // el() filters a null child; replaceChildren stringifies one — so the stale-id option is
      // pushed onto the list rather than passed as a conditional argument.
      const opts = [el("option", { value: "" }, "none"),
        ...domains.map((x) => el("option", { value: x.id }, x.name))];
      if (stored && !domains.some((x) => x.id === stored))
        opts.push(el("option", { value: stored }, `${stored} — missing`));
      sel.replaceChildren(...opts);
      sel.value = stored;
      sel.disabled = false;
      describe();
    } catch {
      sel.replaceChildren(el("option", {}, "unavailable"));
      detail.replaceChildren("could not load the domains");
    }
  })();
  sel.onchange = async () => {
    const target = sel.value;
    sel.disabled = true;
    try {
      // "" is the stored value for no domain; the PATCH drops nulls — so leaving one sends
      // the empty string, never null, which would read as "field omitted" and change nothing.
      await api(`/api/routines/${slug}`, { method: "PATCH", body: { domain: target } });
      const joined = domains.find((x) => x.id === target);
      const left = domains.find((x) => x.id === stored);
      toast(target
        ? `joined ${joined?.name || target} — its config and shared store reach this routine at `
          + "its next run"
        : `left ${left?.name || "the domain"} — its config and shared store are gone from the `
          + "next run");
      setTimeout(remount, 600);   // the panels below re-read the merged truth
    } catch (err) {
      // nothing was saved — put the control back on the stored value rather than leaving it
      // showing a domain this routine is not in
      toastError(err);
      sel.value = stored;
      sel.disabled = false;
      describe();
    }
  };
  view.append(...settingsSection({ title: "Domain", id: "domain" },
    ["the shared surface this routine is part of — at most one, named in this routine's own ",
     "file. Joining does two things: the domain's config is merged UNDER this routine's own ",
     "keys, so ", el("strong", {}, "this routine always wins"), " wherever both set one ",
     "(lists add together); and its shared store becomes a readable AND writable root for ",
     "every run. Both take effect at the NEXT run — the shared config is merged when the ",
     "routine is loaded and the store is injected into the fs roots at boot, so both happen ",
     "once, before the first turn. Leaving takes all of it back the same way. ",
     "A domain's own shared block is edited on the Routines page; its membership is not, ",
     "because it lives in each member's file — this control is where it changes."],
    el("div", { class: "row" }, sel), detail));
}

/** The identity group. `library` is the routine page's ONE /api/library read (that endpoint
 *  lints the whole library per call), shared with the rule picker. */
export function identitySections(view, d, { slug, titleH1, library, refreshSurface }) {
  // -- name (rename; the header + dashboard show it — slug stays the identity) ------
  const nameInput = el("input", { type: "text", value: d.name || slug, placeholder: "routine name",
    style: "width:100%;max-width:420px" });
  view.append(...settingsSection({ title: "Name", id: "name" },
    ["the display name (the folder ", el("span", { class: "ref-tag" }, slug), " stays the identity)"],
      el("div", { class: "row" }, nameInput,
        el("button", { class: "btn primary", onclick: (e) => {
          const v = nameInput.value.trim();
          if (!v) { toast("name can't be empty"); return; }
          act(e.currentTarget, async () => {
            const r = await api(`/api/routines/${slug}`, { method: "PATCH", body: { name: v } });
            titleH1.textContent = v;
            return r;
          }, "name saved");
        } }, "save name"))));

  // -- description (always present; shown here + on the dashboard) ----------------
  const descInput = el("textarea", { rows: "3", placeholder: "what this routine does — a short summary shown on the dashboard and here",
    style: "width:100%;max-width:640px;resize:vertical" }, d.description || "");
  view.append(...settingsSection({ title: "Description", id: "description" },
    "a short summary of what this routine does — shown on the dashboard and here",
      descInput,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: (e) => {
          const v = descInput.value.trim();
          if (!v) { toast("description can't be empty"); return; }
          act(e.currentTarget,
              () => api(`/api/routines/${slug}`, { method: "PATCH", body: { description: v } }),
              "description saved");
        } }, "save description"))));

  // -- tags (shared editor — every add/remove saves immediately) --------------------
  view.append(...settingsSection({ title: "Tags", id: "tags" },
    ["freeform labels for filtering on the dashboard (e.g. meta tucks a routine away by ",
     "default) — each change saves immediately"],
      tagsEditor(d.tags, async (next) => {
        await api(`/api/routines/${slug}`, { method: "PATCH", body: { tags: next } });
        toast("tags saved");
      })));


  // -- settings template: the named starting point the panels below layer over ------------
  // The panel itself lives in components/template-panel.js: picking a template is one control,
  // but READING one — what it supplies, what this routine drops from it, what is set here —
  // is the part that was missing — and it is too much to inline here.
  const tplHost = el("div", {});
  view.append(...settingsSection({ title: "Start from a template", id: "template" },
    ["a named starting point — applying one COPIES its conduct docs, rules and capabilities ",
     "into this routine, once. They become the routine's own: every one is then editable and ",
     "removable in the panel that owns it, and editing the template in the library afterwards ",
     "changes nothing here."],
    tplHost));
  templatePanel(tplHost, slug, d, { library, onApplied: refreshSurface });

  // -- domain: the shared surface, right after the template it reads next to ------------------
  // The two answer the same question — where does this routine's config come from? — in
  // opposite ways: a template COPIES once and the copy is then this routine's own, a domain
  // LAYERS under this file at every load and can be left again.
  domainSection(view, slug, d);


  // -- origin: the library pattern this routine was generated from (provenance only) ----------
  const wf = d.workflow_ref || {};
  view.append(...settingsSection({ title: "Origin", id: "origin" },
    "which library pattern this routine was generated from — provenance, read-only: the recipe "
    + "it produced is the routine's own, edited in the Recipe section.",
      el("span", { class: "ref-tag" }, wf.slug || "hand-authored"),
      el("span", { class: "muted small", style: "margin-left:10px" },
        wf.slug
          ? (wf.in_library
             ? "the library pattern this routine was generated from — its recipe is the routine's OWN now (edit it in the Recipe section)"
             : "its origin pattern is no longer in this library — the recipe is the routine's OWN (edit it in the Recipe section)")
          : "written directly, not generated from a library pattern")));
}
