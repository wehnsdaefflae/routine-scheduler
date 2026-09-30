// The PATTERN leads a routine's settings: which library pattern it follows, how far it departs
// from it, and the three ways to change that — have the system recommend one, save these values
// as a new pattern, or follow another pattern. Plus the banner over a pending proposal.
//
// A pattern is a REFERENCE the page reads the routine's own values against, never a layer: the
// routine's file holds every value; "follow another pattern" only PROPOSES the values that
// pattern carries — they are accepted with the page's one accept, like every other change. So
// nothing in this bar writes a setting; the one write it makes is "Save as new pattern", which
// adds a document to the LIBRARY from values the routine already holds.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { reveal } from "/static/components/settings-field.js";
import { describe } from "/static/components/settings-digest.js";
import { el, toast, toastError, when } from "/static/util.js";

let catalogue = null;                     // GET /api/patterns, once per page
export function loadPatterns() {
  catalogue ??= api("/api/patterns").then((d) => d.patterns || [])
    .catch((err) => { catalogue = null; throw err; });
  return catalogue;
}
export function forgetPatterns() { catalogue = null; }

const REPLACES = "It replaces the changes you have not accepted yet.";

/** Scroll to every field that departs from the pattern — opening the folds they sit in — and
 *  flash them. The first one lands in the middle of the screen. */
function showOverrides() {
  const nodes = [...document.querySelectorAll(".sf-field.sf-override")];
  nodes.slice(1).forEach((n) => reveal(n, { flash: true }));
  reveal(nodes[0]);
}

export function patternBar(form) {
  const head = el("div", { class: "pb-head" });
  const acts = el("div", { class: "pb-acts" });
  const status = el("div", { class: "pb-status" });
  const tray = el("div", { class: "pb-tray" });
  const node = el("div", { class: "pattern-bar", "data-pattern-bar": "" }, head, acts, status, tray);
  let titles = {};
  loadPatterns().then((ps) => { titles = Object.fromEntries(ps.map((p) => [p.slug, p.title])); paint(); })
    .catch(() => { /* the bar reads without titles; the picker says why when opened */ });

  const recommendBtn = el("button", { type: "button", class: "btn", "data-recommend": "",
    onclick: recommend }, "Recommend for this routine");
  const saveBtn = el("button", { type: "button", class: "btn", "data-save-pattern": "",
    onclick: () => openTray(saveForm()) }, "Save as new pattern");
  const followBtn = el("button", { type: "button", class: "btn", "data-follow-pattern": "",
    onclick: () => openTray(followPicker()) }, "Follow another pattern");
  acts.append(recommendBtn, saveBtn, followBtn);

  function openTray(content) {
    tray.replaceChildren(content);
    content.querySelector("input,select,button")?.focus();
  }
  const closeTray = () => tray.replaceChildren();

  function paint() {
    const p = form.pattern;
    const d = form.payload;
    const over = d.overrides || [];
    head.replaceChildren(...[
      el("div", { class: "pb-kicker" }, "settings pattern"),
      el("div", { class: "pb-title" }, p ? p.title : d.pattern_missing ? "its pattern was deleted"
                                                   : "follows no pattern"),
      p ? el("div", { class: "pb-summary prose" }, p.summary) : null,
      p ? el("div", { class: "pb-meta" },
            el("span", {}, "built around the workflow "), el("code", {}, p.workflow || "—"),
            p.when ? el("span", { class: "pb-when" }, ` · pick it when: ${p.when}`) : null)
        : el("div", { class: "pb-summary muted" }, d.pattern_missing
            ? "The pattern this routine named is no longer in the library. Nothing changed: every "
              + "value on this page was always the routine's own. Follow another pattern, or save "
              + "these values as a new one."
            : "Every value on this page is the routine's own, read against no pattern. Have one "
              + "recommended, follow one from the library, or save these values as a new pattern."),
      p ? el("div", { class: "pb-diff" }, over.length
            ? el("button", { type: "button", class: "pb-overrides", "data-overrides": String(over.length),
                title: over.map((k) => form.meta(k)?.label || k).join(", "),
                onclick: showOverrides },
                `${over.length} value${over.length === 1 ? "" : "s"} differ${over.length === 1 ? "s" : ""} from it`)
            : el("span", { class: "muted small", "data-overrides": "0" },
                "every value it carries is the routine's too"))
        : null,
    ].filter(Boolean));
    // Save as new pattern: offered exactly when the routine's SAVED values match no pattern in
    // the library — and only over saved values, because that is what the server copies.
    const pending = form.count() > 0;
    const same = d.identical || [];
    saveBtn.disabled = pending || !d.save_as_enabled;
    saveBtn.title = pending
      ? "accept or discard your changes first — a pattern is saved from the values the routine holds"
      : d.save_as_enabled ? "copy these values into the library as a new pattern this routine follows"
      : p && same.includes(p.slug) ? "these values are exactly its own pattern — nothing new to save"
      : `these values are exactly the pattern ${same.map((s) => titles[s] || s).join(", ")} — follow it instead`;
    const sw = form.patternSwitch();
    status.replaceChildren(...[
      sw ? el("div", { class: "pb-switch", "data-pattern-switch": form.pendingPattern },
        form.pendingPattern
          ? `on accept, this routine follows ${titles[form.pendingPattern] || form.pendingPattern}`
          : "on accept, this routine follows no pattern") : null,
    ].filter(Boolean));
  }

  async function recommend() {
    if (form.count() && !(await confirmDialog(
      `Recommend settings for this routine? ${REPLACES}`, { confirmLabel: "recommend", danger: false }))) return;
    closeTray();
    recommendBtn.disabled = true;
    const started = Date.now();
    const line = el("span", {}, "reading the recipe against the pattern catalogue — this takes a "
      + "minute or two; the page stays usable");
    const clock = el("span", { class: "faint" });
    const tick = setInterval(() => { clock.textContent = ` · ${Math.round((Date.now() - started) / 1000)}s`; }, 1000);
    // the machine working, so SIGNAL — not the amber `.busy` a stalled stream wears
    tray.replaceChildren(el("div", { class: "pb-busy", "data-recommend-busy": "" },
      el("span", { class: "spinner", "aria-hidden": "true" }), el("span", {}, line, clock)));
    try {
      await form.recommend();
      tray.replaceChildren();
      toast(form.proposal ? "a proposal is waiting — each change is marked where it sits"
        : "nothing to change — the recommendation matches what this routine holds", 5000);
    } catch (err) {
      // no response at all (a dropped connection, a proxy timeout) carries no status
      tray.replaceChildren(el("div", { class: "pb-error small", "data-recommend-error": "" },
        err?.status === undefined
          ? "The recommender did not answer — the connection closed before it finished reading "
            + "the whole recipe with the system model. Try again in a moment; everything else on "
            + "this page works without it."
          : `No recommendation: ${err.message}`));
    } finally {
      clearInterval(tick);
      recommendBtn.disabled = false;
    }
  }

  function saveForm() {
    const title = el("input", { type: "text", placeholder: "a name, e.g. Mailbox watcher",
      "data-pattern-title": "", "data-nopersist": true });
    const summary = el("input", { type: "text",
      placeholder: "one line — what a routine on this pattern does", "data-pattern-summary": "",
      "data-nopersist": true });
    const whenIn = el("input", { type: "text", placeholder: "optional — when to pick it",
      "data-nopersist": true });
    const keys = form.payload.save_as_fields || [];
    const boxes = keys.map((k) => ({ k, box: el("input", { type: "checkbox", checked: "",
      "data-nopersist": true }) }));
    const save = el("button", { type: "button", class: "btn primary", "data-pattern-save": "",
      onclick: async () => {
        if (!title.value.trim() || !summary.value.trim()) {
          toast("a pattern needs a name and a one-line summary", 3500, { error: true });
          return;
        }
        save.disabled = true;
        try {
          const r = await form.saveAsPattern({ title: title.value.trim(), summary: summary.value.trim(),
            when: whenIn.value.trim(), fields: boxes.filter((b) => b.box.checked).map((b) => b.k) });
          forgetPatterns();
          loadPatterns().then((ps) => { titles = Object.fromEntries(ps.map((p) => [p.slug, p.title])); paint(); })
            .catch(() => {});
          closeTray();
          toast(`saved as the pattern ${r.pattern?.title || title.value.trim()} — this routine follows it now`, 5000);
        } catch (err) { toastError(err, 6000); save.disabled = false; }
      } }, "save pattern");
    return el("div", { class: "pb-form", "data-pattern-form": "" },
      el("div", { class: "muted small" },
        "The values this routine holds now become a new pattern in the library, which the "
        + "routine follows from then on. A pattern is never edited afterwards — a different set of values is saved as "
        + "another new one."),
      el("label", { class: "field" }, el("span", {}, "name"), title),
      el("label", { class: "field" }, el("span", {}, "summary"), summary),
      el("label", { class: "field" }, el("span", {}, "pick it when"), whenIn),
      el("details", { class: "pb-fields" },
        el("summary", { class: "small" }, `the ${keys.length} settings it carries`),
        el("div", { class: "pb-field-list" }, ...boxes.map(({ k, box }) =>
          el("label", { class: "row small", style: "gap:6px" }, box,
            el("span", {}, form.meta(k)?.label || k),
            el("span", { class: "faint" }, describe(k, form.saved(k))))))),
      el("div", { class: "row", style: "gap:8px" }, save,
        el("button", { type: "button", class: "btn ghost", onclick: closeTray }, "cancel")));
  }

  function followPicker() {
    const box = el("div", { class: "pb-picker", "data-pattern-picker": "" },
      el("div", { class: "muted small" }, "loading the library's patterns…"));
    loadPatterns().then((ps) => {
      const current = form.pattern?.slug || "";
      box.replaceChildren(
        el("div", { class: "muted small" },
          "Following a pattern PROPOSES the values it carries that differ from this routine's. "
          + "Nothing changes until you accept them at the foot of the page."),
        ...ps.map((p) => el("div", { class: `pb-choice${p.slug === current ? " current" : ""}`,
          "data-pattern": p.slug },
          el("div", { class: "pb-choice-main" },
            el("div", { class: "pb-choice-title" }, p.title,
              p.slug === current ? el("span", { class: "faint small" }, " · followed now") : null),
            el("div", { class: "prose small" }, p.summary),
            el("div", { class: "faint small" }, `${p.workflow} · ${(p.followers || []).length} `
              + `routine${(p.followers || []).length === 1 ? "" : "s"} follow it`)),
          p.slug === current ? null
            : el("button", { type: "button", class: "btn small", "data-propose": p.slug,
                onclick: () => follow(p) }, "propose"))),
        el("div", { class: "row" },
          el("button", { type: "button", class: "btn ghost small", onclick: closeTray }, "close")));
    }).catch((err) => box.replaceChildren(el("div", { class: "pb-error small" },
      `could not read the library's patterns: ${err.message}`)));
    return box;
  }

  async function follow(p) {
    if (form.count() && !(await confirmDialog(`Propose following ${p.title}? ${REPLACES}`,
      { confirmLabel: "propose", danger: false }))) return;
    try {
      await form.follow(p.slug);
      closeTray();
      toast(form.proposal ? `following ${p.title} is proposed — check the changes, then accept`
        : `${p.title} carries exactly this routine's values`, 5000);
    } catch (err) { toastError(err); }
  }

  paint();
  form.subscribe(paint);
  return node;
}

/** The banner over a PENDING proposal — creation's "check the changes i recommend.", a
 *  recommendation, or a proposed pattern switch. Hidden when there is none. */
export function draftBanner(form) {
  const node = el("div", { class: "draft-banner", "data-draft-banner": "", hidden: true });
  const SOURCE = { creation: "written when the routine was created", create: "written when the routine was created",
                   recommend: "recommended for this routine", follow: "proposed by a pattern switch" };
  function paint() {
    const p = form.proposal;
    node.hidden = !p;
    if (!p) { node.replaceChildren(); return; }
    const n = Object.keys(p.changes || {}).length;
    node.replaceChildren(...[
      el("div", { class: "db-message" }, p.message || "pending changes"),
      p.reason ? el("div", { class: "db-reason prose" }, p.reason) : null,
      el("div", { class: "db-meta" },
        `${n} change${n === 1 ? "" : "s"}${p.pattern ? " and a pattern switch" : ""} · `,
        SOURCE[p.source] || p.source || "proposed", p.created ? " · " : "",
        p.created ? when(p.created) : null),
      el("div", { class: "db-how small" },
        "Each proposed value is filled into its control below and marked as a change, with the "
        + "reason beside it. Edit or revert any of them; nothing is applied until you press "
        + "accept changes at the foot of the page."),
      el("div", { class: "row", style: "gap:8px" },
        el("button", { type: "button", class: "btn small", onclick: () =>
          reveal(document.querySelector(".sf-field.sf-changed") || document.querySelector("[data-pattern-switch]")) },
          "show the first change"),
        el("button", { type: "button", class: "btn small ghost", "data-discard-proposal": "",
          onclick: async () => {
            try { await form.discardProposal(); toast("proposal discarded — your own edits stay"); }
            catch (err) { toastError(err); }
          } }, "discard proposal")),
    ].filter(Boolean));
  }
  paint();
  form.subscribe(paint);
  return node;
}
