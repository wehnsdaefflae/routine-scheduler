// The Routines page's DOMAINS section — the other axis a routine sits in
// (docs/lanes-domains.md): what a set of routines SHARES, which is one config block, one
// store and one notes boundary. Its own section rather than a column on the table, because a
// domain has nothing to do with when anything fires; lanes are rows in the table for the
// opposite reason.
//
// Membership is NOT editable here, and saying so is half the section's job: a routine names
// its domain in its own routine.yaml, so joining and leaving are an ordinary config save on
// the routine's page. That is what keeps "at most one domain" a fact of the file.
//
// Split out of dashboard.js, which carried this section, the routine card, the detail table
// and the week-strip drag ops in one 850-line closure. `reload` is the page's own load() —
// every mutation here re-reads the page rather than patching a row, because a domain change
// moves the chips on every member's row too.

import { api } from "/static/api.js";
import { confirmDialog, promptDialog } from "/static/components/dialog.js";
import { domainConfigPanel } from "/static/components/domainconfig.js";
import { el, toast, toastError } from "/static/util.js";

/** Returns { body, render, reveal, signature } — `body` is the element to mount, `render`
 *  repaints from the given records, `reveal` opens the section and scrolls one row into view
 *  (a routine's domain chip), and `signature` is the change key the page compares against so
 *  a live refresh never tears down an open config editor (F229). */
export function domainsSection(panel, { reload }) {
  const domainsBody = el("div", {});
  let domains = [];
  let sig = "[]";

  // ---- the domains section ---------------------------------------------------------------
  // Membership is NOT editable here; saying so is half the section's job. A routine names
  // its domain in its own routine.yaml, so joining and leaving are an ordinary config save on
  // the routine's page. That is what keeps "at most one domain" a fact of the file — and it is
  // the first thing someone looks for on this page, so the line is not buried in a tooltip.
  // "library-sync · self-audit · 4 shared settings" — who is in it and how much it hands them,
  // which is the pair that decides whether a domain is doing anything.
  const domainSummary = (d) => {
    const members = d.members || [];
    const n = Object.keys(d.config || {}).length;
    return `${members.length ? members.join(" · ") : "no members"}`
      + ` · ${n} shared setting${n === 1 ? "" : "s"}`;
  };

  function domainRow(d) {
    const counts = el("span", { class: "faint small" }, domainSummary(d));
    const host = el("div", { class: "mt", hidden: true });
    let built = false;
    // The saved record REPLACES the one this row was built from; the signature moves with it
    // too, or the next bus tick would find "changed" data and tear down an open editor.
    const onSaved = (rec) => {
      const at = domains.findIndex((x) => x.id === rec.id);
      if (at >= 0) domains[at] = rec;
      counts.textContent = domainSummary(rec);
      sig = JSON.stringify(domains);
    };
    const edit = el("button", { class: "btn small ghost", "data-domain-edit": "",
      title: "edit the config block this domain's members inherit" },
      "✎ edit");
    edit.onclick = () => {
      if (!built) { built = true; host.append(domainConfigPanel(d, { onSaved })); }
      host.hidden = !host.hidden;
    };
    const ren = el("button", { class: "btn small ghost", "data-domain-rename": "" }, "rename");
    ren.onclick = async () => {
      const name = await promptDialog(`Rename domain “${d.name}”`, { value: d.name });
      if (!name || name === d.name) return;
      try { await api(`/api/domains/${d.id}`, { method: "PATCH", body: { name } });
        toast(`domain renamed to “${name}”`); }
      catch (ex) { toastError(ex); }
      await reload();
    };
    // A domain with members cannot be deleted (409) — every one of them would be left naming
    // nothing and silently narrowed. The server says exactly who is holding it; say that back.
    const del = el("button", { class: "btn small danger", "data-domain-delete": "" }, "delete");
    del.onclick = async () => {
      if (!(await confirmDialog(`Delete domain “${d.name}”? The shared store on disk is kept.`,
        { confirmLabel: "delete" }))) return;
      try { await api(`/api/domains/${d.id}`, { method: "DELETE" }); toast("domain deleted"); }
      catch (ex) { toastError(ex, 6000); }
      await reload();
    };
    return el("div", { class: "mt", "data-domain-row": d.id },
      el("div", { class: "row", style: "gap:8px;align-items:center;flex-wrap:wrap" },
        el("span", {}, `◈ ${d.name}`), counts,
        el("span", { class: "row", style: "gap:6px;margin-left:auto" }, edit, ren, del)),
      host);
  }

  function renderDomains() {
    domainsBody.replaceChildren();
    const add = el("button", { class: "btn small", "data-domain-new": "" }, "＋ new domain");
    add.onclick = async () => {
      const name = await promptDialog("Name the new domain",
        { placeholder: "what these routines have in common" });
      if (!name) return;
      try { await api("/api/domains", { method: "POST", body: { name } });
        toast(`domain “${name}” added`); }
      catch (ex) { toastError(ex); }
      await reload();
    };
    domainsBody.append(
      el("div", { class: "row", style: "gap:8px;align-items:center;flex-wrap:wrap" },
        el("span", { class: "lbl" }, "◈ domains"), add,
        el("span", { class: "muted small" },
          "a routine JOINS a domain on its own page — the domain setting in its config. "
          + "At most one — it is what puts the shared store in the run's roots.")),
      ...(domains.length
        ? domains.map(domainRow)
        : [el("div", { class: "muted small mt" },
            "No domains yet. Make one when two routines should share a permission surface, a "
            + "secret and a store — routines that only need to fire in order want a lane.")]));
  }

  // Where a routine's domain chip goes: open the section (it may be collapsed) and bring that
  // domain's row into view. The rows are built on every load regardless of the panel's state,
  // so the target is always there to scroll to.
  function revealDomain(id) {
    panel.open = true;
    domainsBody.querySelector(`[data-domain-row="${CSS.escape(id)}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return {
    body: domainsBody,
    signature: () => JSON.stringify(domains),
    render(records) { domains = records; sig = JSON.stringify(domains); renderDomains(); },
    changed(records) { return JSON.stringify(records) !== sig; },
    reveal: revealDomain,
  };
}
