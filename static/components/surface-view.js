// The EFFECTIVE SURFACE: every dependency this routine's setup resolves to, the satisfied ones
// included, grouped by what put each one there.
//
// `setupcheck.js` shows the same join filtered to what is UNSATISFIED — deliberately, because a
// strip that is always present is a strip nobody reads. It renders the offer through this
// module's `fixLine`: one map, one renderer, because a second copy of the kind→panel map is a
// second place for it to drift. But the filter leaves the other half unreadable:
// "the secrets this routine can actually reach", "the roots its utils actually need", "which
// conduct doc is the reason it holds this at all" are questions the panels below cannot answer,
// because each panel shows one layer and the answer is the join of all of them.
//
// So this is the same endpoint, unfiltered, ordered worst-first, with each row hung under its
// PROVENANCE — `node.source` is machine-readable for exactly this (`{doc}` / `{utils}`), which
// is why grouping here never has to parse the prose in `why`.
//
// An unmet row also carries `node.fix`. It is SEMANTIC: a `kind` plus that kind's parameters,
// naming what needs DOING and never where a console puts the control. `rsched validate` renders
// this same read model on a terminal, where a section id means nothing. So the map from a kind to
// a panel belongs to the client; it is FIX below. Its words are held to the CLI's own remedy for
// the same kind (`REMEDIES`, readmodels/remedies.py): the two halves address different readers,
// so the console being the vaguer of the two is a defect.
//
// An offer is made only where the act can be PERFORMED. A link that lands on a panel whose
// control is absent, disabled, or structurally unable to hold this row is worse than no link:
// the flash fires, the reader hunts for a control that was never there, and the next honest
// diagnosis on the page is read as decoration. So a `focus` addresses the attribute the owning
// panel stamps on the control that does the thing; a row whose act belongs to a different
// surface names that surface instead of offering the act here.

import { api } from "/static/api.js";
import { LABEL } from "/static/components/setupcheck.js";
import { el } from "/static/util.js";

function sourceKey(node) {
  const src = node.source || {};
  if (src.doc) return `doc:${src.doc}`;
  if (src.utils?.length) return `util:${src.utils.join(", ")}`;
  return "";
}

function sourceLabel(key) {
  if (key.startsWith("doc:")) return [`from the conduct doc `, el("code", {}, key.slice(4))];
  if (key.startsWith("util:")) return [`declared by `, el("code", {}, key.slice(5))];
  return ["from this routine's own config"];
}

// Entity ids are `<class>:<name>`; a label reads the name, because the class is already the
// column to its left. A bare slug passes through unchanged, which is what `switch_on` carries.
const plain = (eid) => String(eid ?? "").split(":").slice(1).join(":") || String(eid ?? "");

// `*` is the server's "any instance of this class will do" — an `expects:` row that names no
// machine or provider, the same case an empty `add_root` path makes (`_ANY_PARAM` in
// readmodels/remedies.py reads both the wildcard and a missing param that way). It is a query
// term, never a name: "bind *" is not an instruction to anybody.
const named = (v) => (String(v ?? "") && String(v) !== "*" ? String(v) : null);

// PROVENANCE, for the fixes whose act belongs to whoever switched the thing on: `domain` names
// the domain a capability was inherited from, absent when the routine owns it outright.
const fromDomain = (f) => named(f.domain);

// A selector for the ONE control a fix names, addressed by the attribute the owning panel
// stamps it with. Absent params yield null, which lands on the panel instead.
const byAttr = (attr, value) => (value ? `[${attr}=${JSON.stringify(String(value))}]` : null);

// kind → what to DO about it plus which panel does it. `section` is an id on this page and
// `focus` a selector for the control inside that panel, so a jump lands on the thing that
// performs the act rather than on the block around it; `href` leaves the page. No section and
// no href means the fix has no control to land on and the words are the whole answer. `alt` is
// the row's second way out where it honestly has two — the wider blast radius, or the half
// nobody at a keyboard performs. It takes the same fields, so an alt naming neither a section
// nor an href is a statement standing in the same line as an offer. The map is BOUND to the
// server's vocabulary rather than a subset of it: `tests/ui/test_surface_fix.py` reads these
// keys and asserts they are exactly the kinds `remedies.REMEDIES` words — and exactly the kinds
// its CASES table drives to a control that performs the act. A kind the server gains is added
// here in the same change, or the suite reds instead of a reader. The render stays guarded all
// the same — an unmapped kind draws nothing, because a row that stays silent beats one that
// points somewhere wrong.
const FIX = {
  // The dial that closes this lives INSIDE the ability card for the doc that asked for it.
  // abilities.js stamps every card `data-ability="<slug>"` for exactly this jump — a panel-level
  // flash lights up every ability at once, which is the hunt the row exists to end.
  switch_on: (f) => ({
    act: `switch on ${(f.missing || []).join(", ") || "what it requires"}`,
    where: "Permissions & capabilities", section: "sec-permissions",
    focus: byAttr("data-ability", plain(f.entity)) }),
  // The entity keeps its CLASS: `util:spawn` and `action:spawn` are two different things to
  // hold. An uncovered capability has two ways out; only the covering half is always this
  // routine's. DROPPING it belongs to whoever switched it on — a capability the routine owns
  // goes off in the orphan card of the permissions panel, one a DOMAIN hands down survives this
  // routine's save (the floor counts inherited permissions, because that capability is the
  // domain's to drop) and goes off in the domain editor. `domain` on the fix carries that, so
  // an inherited row names the domain rather than offering a switch that is not on this page.
  cover_or_drop: (f) => (fromDomain(f)
    ? { act: `hold a doc that requires ${f.entity}`,
        where: "Permissions & capabilities", section: "sec-permissions",
        alt: { act: `drop it from the ${fromDomain(f)} domain`,
               where: "the Routines page", href: "#/routines" } }
    : { act: `drop ${f.entity}, or hold a doc that requires it`,
        where: "Permissions & capabilities", section: "sec-permissions",
        focus: byAttr("data-drop", f.entity) }),
  grant: (f) => ({
    act: `expose or withhold ${plain(f.entity)}`,
    where: "Secret exposure", section: "sec-secret-exposure",
    focus: byAttr("data-secret-row", plain(f.entity)) }),
  // A denied SECRET is a `withhold` row in Secret exposure, which is the only class this kind is
  // ever emitted for. Declined access holds every OTHER class, so a link there would flash a
  // list this row can never appear in.
  clear_grant: (f) => ({
    act: `clear the withhold on ${plain(f.entity)}`,
    where: "Secret exposure", section: "sec-secret-exposure",
    focus: byAttr("data-secret-row", plain(f.entity)) }),
  add_secret: (f) => ({
    act: `add ${f.name}`,
    where: "Settings → Secrets", href: "#/settings?section=secrets" }),
  // An `expects:` row may name no path at all (the prose wants A write root, any of them).
  add_root: (f) => ({
    act: named(f.path) ? `add a ${f.mode || "read"} root covering ${f.path}`
                       : `add a ${f.mode || "read"} root`,
    where: "Filesystem roots", section: "sec-fs-roots" }),
  bind_machine: (f) => ({
    act: named(f.name) ? `bind ${f.name}` : "bind a machine",
    where: "Machines", section: "sec-machines" }),
  bind_connection: (f) => ({
    act: named(f.provider) ? `connect ${f.provider}` : "connect an account",
    where: "Connections", section: "sec-connections" }),
  // A util is written by a RUN through write_util. The Library page offers "+ new" for rules,
  // permissions and templates; for utils it offers none, because nothing authors one by hand.
  // So the half a person performs is the other one — stop holding the name — and WHICH act
  // that is depends on what holds it, exactly as `cover_or_drop` splits. A held doc that
  // requires the util raises the name back at the next save, so the drop is not performable at
  // all: the offer names that doc (`doc` on the fix) and lands on ITS card, the one control
  // that settles the row. A capability the DOMAIN supplies survives this routine's save and
  // comes off in the domain's editor. Only what this routine owns outright is dropped in the
  // orphan card here. The half nobody can press rides along as words with no control behind
  // them, which is what a spec carrying neither a section nor an href renders as.
  install_util: (f) => {
    const alt = { act: `a run writes ${f.name} through write_util` };
    if (named(f.doc)) {
      return { act: `untick ${f.doc}, the doc that requires ${f.name}`,
               where: "Permissions & capabilities", section: "sec-permissions",
               focus: byAttr("data-ability", f.doc), alt };
    }
    if (fromDomain(f)) {
      return { act: `drop ${f.name} from the ${fromDomain(f)} domain`,
               where: "the Routines page", href: "#/routines", alt };
    }
    return { act: `drop ${f.name}`,
             where: "Permissions & capabilities", section: "sec-permissions",
             focus: byAttr("data-drop", `util:${f.name}`), alt };
  },
  set_schedule: () => ({
    act: "give it a schedule", where: "Schedule", section: "sec-schedule" }),
  // The complaint is that THIS routine's file records a cron it will never fire at, so the fix
  // that touches only this routine is clearing that cron. Its lane's schedule is the other way
  // out and it is instance state every member fires on — rescheduling the lane to repair one
  // stale line here moves when the other members run — so it is the second offer, in the order
  // the CLI remedy words the pair. A lane carries a human name beside its id; the name is what
  // the Routines page, which owns the lane, lists it by (docs/lanes-domains.md).
  //
  // The frequency select is locked in the lane-managed state this row is emitted under, so the
  // landing is the panel's own clear-cron control (`data-clear-cron`) — the one thing in there
  // that moves while a lane is scheduling this routine.
  lane_schedule: (f) => ({
    act: "clear this routine's cron", where: "Schedule", section: "sec-schedule",
    focus: "[data-clear-cron]",
    alt: { act: named(f.name) || named(f.lane)
             ? `reschedule the ${named(f.name) || named(f.lane)} lane` : "reschedule its lane",
           where: "the Routines page", href: "#/routines" } }),
  fix_phase: (f) => ({
    act: `record ${f.expected || "phase"} in state/phase.json`, where: "the recipe" }),
};

/** Fully on screen. The second scroll is worth making only for a control the first one left off
 *  screen — a short panel's control is already in front of the reader, so moving the page again
 *  would take the heading away for nothing. */
function inViewport(node) {
  const r = node.getBoundingClientRect();
  return r.height > 0 && r.top >= 0 && r.bottom <= window.innerHeight;
}

// The landing half, shaped after `reflinks.focusRef` — scroll centred, flash, drop the flash
// after 2500ms. It cannot BE focusRef: that one addresses item cards by `ref-<id>`; these
// anchors are the section headings' own `sec-*` ids. `focus` addresses ONE control inside the
// panel — the ability card carrying the dial, the exposure row carrying the select. The section
// is scrolled to first and the control only if that left it off screen, by the LEAST scroll that
// brings it into view, so the heading stays overhead as context wherever both fit. The flash
// then moves from the title to the panel and the control: two outlines say "this section, that
// control", three say nothing. Any collapsed group above is opened first — a jump into a folded
// <details> lands nowhere.
function jumpToSection(id, focus) {
  const heading = document.getElementById(id);
  if (!heading) return false;
  const panel = heading.nextElementSibling?.classList.contains("panel")
    ? heading.nextElementSibling : null;
  const control = focus && panel ? panel.querySelector(focus) : null;
  const start = control || heading;
  for (let d = start.closest("details"); d; d = d.parentElement?.closest("details")) d.open = true;
  heading.scrollIntoView({ block: "center" });
  // The control is what the reader was sent to, so it is what gets centred — `nearest` does the
  // minimum and parks a 22px button flush against the bottom edge, where the row it sits in is
  // half cut off and the reader has to scroll again to read what they landed on. `center` is
  // also what reflinks.js does for the same journey.
  if (control && !inViewport(control)) control.scrollIntoView({ block: "center" });
  for (const node of (control ? [panel, control] : [heading, panel]).filter(Boolean)) {
    node.classList.add("ref-flash");
    setTimeout(() => node.classList.remove("ref-flash"), 2500);
  }
  return true;
}

function fixControl(spec) {
  if (spec.href) {
    return el("a", { class: "fix-link fix-away", href: spec.href,
      title: `${spec.act} — ${spec.where}, away from this routine` }, spec.act);
  }
  if (!spec.section) return el("span", { class: "fix-act" }, spec.act);
  const onclick = (e) => {
    if (jumpToSection(spec.section, spec.focus)) return;
    e.currentTarget.disabled = true;
    e.currentTarget.title = `${spec.where} is not on this page`;
  };
  return el("button", { type: "button", class: "fix-link", "data-fix-section": spec.section,
    title: `${spec.act} — ${spec.where}, on this page`, onclick }, spec.act);
}

/** The line under the diagnosis that answers it — and the one renderer of it, because
 *  `setupcheck.js` puts the same offer on the same rows at the top of the page. Two copies of a
 *  kind→panel map are two maps. Null for a satisfied row: an affordance on a row that is already
 *  fine invites clicking to check; the panel's worth is that it reads without touching it. Null
 *  too where the server names no fix, which is a row stating how the routine is SET rather than
 *  a gap in its setup. */
export function fixLine(node) {
  const fix = node.fix || {};
  const build = node.severity === "ok" ? null : FIX[fix.kind];
  if (!build) return null;
  const spec = build(fix);
  return el("div", { class: "fix-line", "data-fix": fix.kind },
    fixControl(spec), el("span", { class: "fix-where" }, spec.where),
    spec.alt ? el("span", { class: "fix-or" }, "or") : null,
    spec.alt ? fixControl(spec.alt) : null,
    spec.alt?.where ? el("span", { class: "fix-where" }, spec.alt.where) : null);
}

function row(node) {
  return el("tr", { class: `sev-${node.severity}`, "data-surface-row": node.id },
    el("td", {}, el("span", { class: "setup-sev" }, LABEL[node.severity] || node.severity)),
    el("td", {}, el("code", {}, node.id)),
    el("td", { class: "muted" }, node.state || ""),
    // The fix rides the PROSE cell rather than a column of its own: it answers the sentence
    // directly above it; most rows are satisfied, so a fifth column would be mostly blank.
    el("td", { class: "muted prose" }, node.why || "",
      node.effect ? el("div", { class: "faint small" }, node.effect) : null,
      fixLine(node)));
}

/** Renders into `host`. The panel EDITS nothing: every row is changed in the panel that owns it
 *  — a root in Filesystem roots, a secret in Secret exposure — because a second place to change
 *  one value is a second place for it to be wrong. It POINTS at that panel instead: an unmet row
 *  names the act and lands you on the control that performs it — the ability card holding the
 *  dial, the exposure row holding the select, the owning panel where the panel is the one
 *  control — so "fixed elsewhere" is an instruction.
 *
 *  Which makes `refresh` load-bearing rather than housekeeping: the reader performs the act in
 *  another panel and comes back to this one. A diagnosis left standing after its own remedy
 *  tells somebody who did exactly what they were told that they did not — then offers them a
 *  button aiming at a control the other panel's repaint has already removed. So the caller
 *  re-reads after every change that can alter the join (`views/routine-config.js`).
 *
 *  `surface` seeds the FIRST paint from a read the page has already made, so mounting costs no
 *  second request. `refresh(data)` takes the same payload from a later shared re-read; with no
 *  argument it reads the endpoint itself. */
export function surfaceView(host, slug, surface = null) {
  const body = el("div", { "data-surface-view": "" });
  host.append(body);

  function paint(data) {
    const nodes = data.nodes || [];
    if (!nodes.length) {
      body.replaceChildren(el("div", { class: "faint small" },
        "nothing resolves yet — this routine holds no conduct doc, rule or reserved util that "
        + "declares a dependency."));
      return;
    }
    const v = data.verdict || {};
    const groups = new Map();
    for (const n of nodes) {
      const key = sourceKey(n);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(n);
    }
    body.replaceChildren(el("div", { class: "muted small" },
      `${nodes.length} resolved dependenc${nodes.length === 1 ? "y" : "ies"} · `,
      el("b", {}, v.blocks ? `${v.blocks} will fail` : "nothing will fail"),
      v.interrupts ? ` · ${v.interrupts} will interrupt` : "",
      v.notes ? ` · ${v.notes} note${v.notes > 1 ? "s" : ""}` : "",
      el("div", { class: "faint" },
        "recomputed on every read — the library moves under a routine, so a stored answer "
        + "would be stale the first time somebody ran write_util.")));
    // Own-config rows last: an operator scanning for "why does it hold this?" is looking for
    // a doc or a util; the rows with no source are the ones they already know about.
    for (const [key, rows] of [...groups].sort((a, b) => (a[0] ? 0 : 1) - (b[0] ? 0 : 1))) {
      body.append(
        el("div", { class: "tpl-head" }, ...sourceLabel(key)),
        el("div", { class: "tablewrap" },
          el("table", { class: "list surface-table" }, el("tbody", {}, ...rows.map(row)))));
    }
  }

  const refresh = async (data = null) => {
    if (data) { paint(data); return; }
    try { paint(await api(`/api/routines/${slug}/surface`)); }
    catch { body.replaceChildren(el("div", { class: "faint small" }, "surface unavailable")); }
  };
  if (surface) paint(surface); else refresh();
  return { refresh };
}
