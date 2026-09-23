// Routine config sections — the orchestrator, and the panels that read the SETUP SURFACE.
//
// What stays here is what refreshSurface serves: the recommended-setup second opinion, the
// permissions and general-rules panels, the surface panel itself, and the group of ceilings
// (goal, budgets, retention, filesystem roots). The other four groups are their own modules,
// cut along routine.js's own SECTION_GROUPS so that one module is one labelled group on the
// page: routine-config-schedule.js (when it fires), routine-config-identity.js (what it is,
// where it came from), routine-config-models.js (models and resource bindings),
// routine-config-access.js (secrets and settled denials).
//
// Every section is built by the shared settingsSection primitive in its { title, id } form, so
// each heading carries a stable `sec-<id>` anchor: the address a link elsewhere on the page
// uses to land the reader on the panel that OWNS a value (the effective surface diagnoses a
// dependency in one place and the dial for it lives in exactly one other). Ids are therefore a
// contract, not decoration — rename a heading freely, keep its id.
//
// Every panel that WRITES also re-reads the SETUP SURFACE, because every panel here can settle
// or open one of its rows. That join has three readers on this page — the strip above the hero,
// the ability cards, the effective-surface panel — and exactly one writer, which is this file.
// So the re-read sits here as `refreshSurface`: one request per change, its answer handed to
// all three, and no reader left holding a diagnosis of the state before the save. It is passed
// DOWN to each section module for the same reason. routine.js owns the strip and passes
// `repaintSetup` in, the same way it passes the header nodes `refreshHead` writes back into.

import { api } from "/static/api.js";
import { act, el, toast, toastError } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { abilitiesPanel } from "/static/components/abilities.js";
import { createStopping } from "/static/components/stopping.js";
import { recommendPanel } from "/static/components/recommend.js";
import { rootsEditor } from "/static/components/fsroots.js";
import { rulePicker } from "/static/components/rulepicker.js";
import { surfaceView } from "/static/components/surface-view.js";
import { BUDGET_FIELDS, UNLIMITED_BUDGETS } from "/static/components/budgetfields.js";
import { accessSections } from "/static/views/routine-config-access.js";
import { identitySections } from "/static/views/routine-config-identity.js";
import { modelSections } from "/static/views/routine-config-models.js";
import { scheduleSections } from "/static/views/routine-config-schedule.js";

const INHERIT_LABEL = {
  permissions: "permissions", capabilities: "capabilities", rules: "general rules",
  machines: "machines", tags: "tags", models: "models", connections: "connections",
  grants: "secret grants", budgets: "budgets",
  fs_read_roots: "readable roots", fs_write_roots: "writable roots",
};

/** D82: a banner naming what this routine got from its DOMAIN, so an inherited value is never
 *  mistaken for one set here. The panels below stay as they are — they show the EFFECTIVE
 *  config, which is what the run actually gets. */
function inheritedNote(d) {
  const fields = Object.keys(d.inherited || {});
  if (!fields.length) return null;
  return el("div", { class: "panel mt", "data-inherited-note": "" },
    el("div", { class: "small" },
      el("b", {}, "Some settings below come from the domain"),
      d.inherited_from ? ` “${d.inherited_from}”` : "", "."),
    el("div", { class: "muted small", style: "margin-top:4px" },
      fields.map((f) => `${INHERIT_LABEL[f] || f} (${d.inherited[f]})`).join(" · ")),
    el("div", { class: "muted small", style: "margin-top:4px" },
      "The panels show the EFFECTIVE config — what this routine actually runs with. Saving "
      + "here writes only this routine's OWN values, and its own value wins wherever it sets "
      + "one. What the domain supplies stays the domain's: a permission marked “from domain” "
      + "cannot be removed here, only in that domain's editor on the Routines page."));
}

export function renderConfigSections(view, d, {
  slug, titleH1, chipHost, runChip, repaintSetup,
}) {
  // ONE read of /api/library for the whole page. Two panels want it — the rule picker here and
  // the template picker in the identity group — and that endpoint LINTS the whole library on
  // every call (≈4.3 s on the fleet), so fetching it twice per routine-page open, and once more
  // per rule save, was the single most expensive thing this page did. The library's rules and
  // templates do not change because this routine's bindings did, so one read serves every
  // repaint too.
  const library = api("/api/library").catch(() => ({ rules: [], templates: [] }));
  const note = inheritedNote(d);
  if (note) view.append(note);

  /** Re-read the surface and repaint every reader of it. Called by each save on this page that
   *  can move a row — permissions, rules, grants, roots, machines, connections, the schedule,
   *  the goal — because the reader who performed a fix is looking at the diagnosis that sent
   *  them, whose button now aims at a control that panel's repaint has removed.
   *
   *  ONE request, then three repaints: this page's surface panel, the strip above the hero
   *  (which routine.js owns, so it hands the repaint down), and `d.surface`, which is what the
   *  ability cards are rebuilt from. A read that FAILS hands each reader a null, which is its
   *  instruction to read for itself and say so if it cannot — showing the pre-save answer as
   *  though the save never happened is the one outcome worth two extra requests to avoid. */
  let surfacePanel = null;
  let surfaceRead = 0;                  // two quick saves: the older answer must not land last
  async function refreshSurface() {
    const mine = ++surfaceRead;
    let next = null;
    try { next = await api(`/api/routines/${slug}/surface`); }
    catch { /* each reader repaints from its own read, or renders unavailable */ }
    if (mine !== surfaceRead) return;
    d.surface = next;
    surfacePanel?.refresh(next);
    repaintSetup?.(next);
  }

  const { refreshHead } = scheduleSections(view, d, { slug, chipHost, runChip, refreshSurface });
  identitySections(view, d, { slug, titleH1, library, refreshSurface });

  // -- recommended setup: the INVERSE of the surface — what SHOULD this routine hold, and why --
  // A second reading of the recipe against the two panels below: given what this routine DOES,
  // which rules and permissions it should hold, each suggested change carrying a one-line why.
  // Advisory only — the panels below are where a toggle actually changes.
  view.append(...settingsSection({ title: "Recommended setup", id: "recommended-setup" },
    ["a second opinion on the two panels below. Given what this routine DOES — its recipe — it ",
     "judges which general rules and permissions it should hold, and lists only the suggested ",
     "changes, each with a one-line reason. Nothing here is applied: you change anything in the ",
     "Permissions and General rules panels below."],
      recommendPanel(slug)));


  // -- permissions: conduct docs + machine-enforced capabilities (user-only) --------
  // The server re-applies the activation cascade on save, so the panel re-renders from a
  // fresh detail read IN PLACE — the old full page reload is gone.
  const permHost = el("div", {});
  const buildPermPanel = (perms, caps) => abilitiesPanel(perms, caps, {
    surface: d.surface,
    onSave: async (payload) => {
      try {
        await api(`/api/routines/${slug}/permissions`, { method: "PUT", body: payload });
        toast("permissions saved");
        const nd = await api(`/api/routines/${slug}`);
        // before the repaint, not after: the cards hang each resolved need under the ability
        // that owns it, so they are a reader of the surface as much as the panels are.
        await refreshSurface();
        permHost.replaceChildren(buildPermPanel(nd.permissions, nd.capabilities));
      } catch (err) { toastError(err); }
    },
  }).node;
  permHost.append(buildPermPanel(d.permissions, d.capabilities));
  view.append(...settingsSection({ title: "Permissions & capabilities", id: "permissions" },
    ["what this routine is ALLOWED to do — enforced by the engine on every action. One card per ",
     "ability, carrying everything that ability needs: the action kinds and reserved utils it ",
     "requires, the secrets, roots and bindings it resolves to, plus its POLICY DIAL where it ",
     "has one — how deep it may read previous runs, who approves a util or rule change, which ",
     "reminder stores it writes to. Enforcement reads the capabilities, not the conduct doc, so ",
     "an ability whose requirements are not all switched on fails closed — its card says so, ",
     "with the dial that fixes it inside that card. Only you can change any of this — a routine ",
     "can never grant itself anything. Takes effect at the next run."],
      permHost));

  // -- general rules (routine.yaml's `rules:` IS the state; the prose is in the library) --
  const ruleHost = el("div", {});
  const buildRulePanel = async (detail) => {
    const lib = await library;
    return rulePicker(lib.rules || [], detail.rules || [], {
      live: !!detail.active_run,
      onSave: async (payload) => {
        await api(`/api/routines/${slug}/rules`, { method: "POST", body: payload });
        const nd = await api(`/api/routines/${slug}`);
        ruleHost.replaceChildren(await buildRulePanel(nd));
        refreshSurface();   // a rule's expects: rows appear and disappear with the binding
      },
    }).node;
  };
  buildRulePanel(d).then((n) => ruleHost.replaceChildren(n));
  view.append(...settingsSection({ title: "General rules", id: "general-rules" },
    ["the rules this routine reads before the situations they govern. Each states a ",
     "principle the run applies to its own case; the prose lives once in the library, so ",
     "editing it there reaches every routine holding it. Binding one reaches a run already ",
     "in flight, unbinding takes effect at the next run. A run can READ any rule (read_rule) ",
     "but never change this set."],
      ruleHost));


  // -- effective surface: the whole join, read-only, satisfied rows included --------
  // The setup-check strip above shows only what is UNMET (a strip that is always there is a
  // strip nobody reads). That leaves "what does this add up to when it IS satisfied?" with no
  // answer anywhere, because every panel above shows exactly one layer.
  const surfaceHost = el("div", {});
  view.append(...settingsSection({ title: "Effective surface", id: "effective-surface" },
    ["every dependency this routine's setup resolves to — secrets, roots, machines, ",
     "connections, reserved utils — with the conduct doc or util that declares each one. ",
     "Read-only, never a dead end: nothing is changed here, because a second place to change ",
     "one value is a second place for it to be wrong — an UNMET row instead names the remedy ",
     "and, where a panel owns the dial, takes you to it: a panel on this page, or wherever ",
     "else in the console that one value lives."],
    surfaceHost));
  // `d.surface` is the fetch routine.js already made for the strip and the ability cards —
  // one read feeds all three readers rather than three requests for one answer.
  surfacePanel = surfaceView(surfaceHost, slug, d.surface);

  // -- budgets (per-run ceilings — every invisible limit, surfaced) -----------------
  const budgetInputs = {};
  const budgetRows = BUDGET_FIELDS.map(([key, label, help]) => {
    const input = el("input", { type: "number", min: UNLIMITED_BUDGETS.includes(key) ? "-1" : "0",
      value: String(d.budgets?.[key] ?? ""), style: "width:110px" });
    budgetInputs[key] = input;
    return el("div", { class: "row", style: "margin:5px 0" },
      input,
      el("span", { style: "min-width:220px" }, label),
      el("span", { class: "muted small" }, help));
  });
  // -- goal: the MEANING-level bounds (F334/D98), directly above the budgets they are not --
  // The panel existed only in a RUN's rail, so a routine that had never run had no surface for
  // its stopping conditions at all; one that had meant opening a run to find them. It
  // belongs on the routine, beside the budgets — the pairing is the point: budgets are a
  // runaway backstop; this is what actually decides when a job is finished.
  const goalHost = el("div", {});
  view.append(...settingsSection({ title: "Goal", id: "goal" },
    ["what DONE means for one run, in your own words — conditions the run must account for in ",
     "its finish summary (`[s1] met — …`), combined with all/any and optionally scoped to a ",
     "stage. Reported, never enforced: the engine judges no semantics, it makes them impossible ",
     "to ignore. Without any, a run is bounded only by its budgets."],
    goalHost));
  // showStage: a per-stage condition is a ROUTINE concept — a conversation has no stages.
  // The verdict is reported on every paint, so the FIRST one is the stored state rather than a
  // change; only a flip is a surface event — meeting the last goal condition retires the
  // routine (`schedule:goal`) and reopening one takes that row away again.
  let goalSatisfied;
  createStopping(goalHost, { url: `/api/routines/${slug}/stopping`, showStage: true,
    onVerdict: (v) => {
      const before = goalSatisfied;
      goalSatisfied = v?.goal_satisfied ?? null;
      if (before !== undefined && before !== goalSatisfied) refreshSurface();
    } });

  view.append(...settingsSection({ title: "Budgets", id: "budgets" },
    ["hard per-run ceilings, checked at every turn — the run is told at 85% so it can wind down ",
     "deliberately. Resources, not permissions."],
      ...budgetRows,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: (e) => {
          const budgets = {};
          for (const [key, input] of Object.entries(budgetInputs)) {
            const v = parseInt(input.value, 10);
            const unlimitedOk = UNLIMITED_BUDGETS.includes(key) && v === -1;
            if (!Number.isFinite(v) || (v < 1 && !unlimitedOk)) {
              toast(`${key}: needs a positive number${UNLIMITED_BUDGETS.includes(key) ? " (or -1 = unlimited)" : ""}`);
              return;
            }
            budgets[key] = v;
          }
          act(e.currentTarget,
              () => api(`/api/routines/${slug}`, { method: "PATCH", body: { budgets } }),
              "budgets saved");
        } }, "save budgets"))));

  // -- retention: how many finished run dirs to keep ------------------------------
  const keepRunsIn = el("input", { type: "number", min: "1", value: String(d.keep_runs ?? 30), style: "width:110px" });
  view.append(...settingsSection({ title: "Retention", id: "retention" },
    ["how many finished run directories to keep — older ones are pruned (transcripts gzip first). ",
     "The durable usage stream (spend, health) survives pruning."],
      el("div", { class: "row" }, keepRunsIn, el("span", {}, "runs kept"),
        el("button", { class: "btn primary", onclick: (e) => {
          const n = parseInt(keepRunsIn.value, 10);
          if (!Number.isFinite(n) || n < 1) { toast("keep at least 1 run"); return; }
          act(e.currentTarget,
              () => api(`/api/routines/${slug}`, { method: "PATCH", body: { keep_runs: n } }),
              "retention saved");
        } }, "save retention"))));


  // -- filesystem roots: extra dirs the run may read / write (resources, not capabilities) --
  // Real server paths, so each is chosen with the server-side directory browser (fsroots.js →
  // dirpicker.js) rather than typed blind; value() yields the path list the PATCH expects.
  const readRoots = rootsEditor(d.fs_read_roots, { pickTitle: "add a read root" });
  const writeRoots = rootsEditor(d.fs_write_roots, { pickTitle: "add a write root" });
  view.append(...settingsSection({ title: "Filesystem roots", id: "fs-roots" },
    ["extra directories this routine may access beyond its own dir — browse to each. Every util ",
     "subprocess is jailed to these roots intersected with what the util itself declares, so a ",
     "path missing here is a path the run cannot reach at all. ",
     el("strong", {}, "Write roots are powerful"), ": a write root that covers this routine's own ",
     "directory unlocks editing its OWN recipe (main.md / stages / tuning.yaml) — the same ",
     "lever the routine-improver holds. routine.yaml stays sealed regardless. Takes effect next run."],
      el("div", { class: "field" }, el("span", {}, "read roots"), readRoots.node),
      el("div", { class: "field mt" }, el("span", {}, "write roots"), writeRoots.node),
      el("div", { class: "row mt" }, el("button", { class: "btn primary", onclick: (e) => {
        act(e.currentTarget, async () => {
          const r = await api(`/api/routines/${slug}`, { method: "PATCH",
            body: { fs_read_roots: readRoots.value(), fs_write_roots: writeRoots.value() } });
          refreshSurface();   // every fs-read:/fs-write: row is a containment test on these
          return r;
        }, "filesystem roots saved");
      } }, "save roots"))));


  modelSections(view, d, { slug, refreshSurface });
  const access = accessSections(view, d, { slug, refreshSurface });

  return { refreshHead, refreshSurface, dispose: access.dispose };
}
