// Routine config sections (Name .. Origin): every user-editable panel of the routine
// page - rename, description, tags, schedule, triggers, schedule-once, domain, permissions,
// practice modules, budgets, retention, fs roots, models + deliberation, connections,
// machines, and origin. Split from routine.js; returns { refreshHead } (the in-place
// header/next-fire refresher the run-lifecycle bus handler calls).
//
// Every section is built by the shared settingsSection primitive in its { title, id } form, so
// each heading carries a stable `sec-<id>` anchor: the address a link elsewhere on the page
// uses to land the reader on the panel that OWNS a value (the effective surface diagnoses a
// dependency in one place and the dial for it lives in exactly one other). Ids are therefore a
// contract, not decoration — rename a heading freely, keep its id.
//
// Every panel here that WRITES also re-reads the SETUP SURFACE, because every panel here can
// settle or open one of its rows. That join has three readers on this page — the strip above the
// hero, the ability cards, the effective-surface panel — and exactly one writer, which is this
// file. So the re-read sits beside the writes as `refreshSurface`: one request per change, its
// answer handed to all three, and no reader left holding a diagnosis of the state before the
// save. routine.js owns the strip and passes `repaintSetup` down, the same way it passes the
// header nodes `refreshHead` writes back into.

import { BUDGET_FIELDS, UNLIMITED_BUDGETS } from "/static/components/budgetfields.js";
import { api } from "/static/api.js";
import { connectionsCard } from "/static/components/connections.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { el, skeleton, toast, when } from "/static/util.js";
import { machinesCard } from "/static/components/machines.js";
import { abilitiesPanel } from "/static/components/abilities.js";
import { rootsEditor } from "/static/components/fsroots.js";
import { routineSecretsCard } from "/static/components/routine-secrets.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { scheduleOnceCard } from "/static/components/schedule-once.js";
import { createStopping } from "/static/components/stopping.js";
import { settingsSection } from "/static/components/settings-section.js";
import { surfaceView } from "/static/components/surface-view.js";
import { tagsEditor } from "/static/components/tags.js";
import { templatePanel } from "/static/components/template-panel.js";
import { rulePicker } from "/static/components/rulepicker.js";
import { recommendPanel } from "/static/components/recommend.js";
import { triggersCard } from "/static/components/triggers.js";

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
      "The panels show the EFFECTIVE config — what this routine actually runs with. Editing "
      + "here changes only this routine's own value, which always wins; change the shared part "
      + "in the domain's editor on the Routines page."));
}

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
 *  A save reloads the page: every panel below shows the EFFECTIVE config, produced by a merge
 *  the server does when it loads the routine — so a stale page would keep showing the surface
 *  of the domain just left. */
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
      setTimeout(() => location.reload(), 600);   // the panels below re-read the merged truth
    } catch (err) {
      // nothing was saved — put the control back on the stored value rather than leaving it
      // showing a domain this routine is not in
      toast(err.message, 4000, { error: true });
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

export function renderConfigSections(view, d, {
  slug, titleH1, chipHost, runChip, repaintSetup,
}) {
  const note = inheritedNote(d);
  if (note) view.append(note);
  // -- name (rename; the header + dashboard show it — slug stays the identity) ------
  const nameInput = el("input", { type: "text", value: d.name || slug, placeholder: "routine name",
    style: "width:100%;max-width:420px" });
  view.append(...settingsSection({ title: "Name", id: "name" },
    ["the display name (the folder ", el("span", { class: "ref-tag" }, slug), " stays the identity)"],
      el("div", { class: "row" }, nameInput,
        el("button", { class: "btn primary", onclick: async () => {
          const v = nameInput.value.trim();
          if (!v) { toast("name can't be empty"); return; }
          try {
            await api(`/api/routines/${slug}`, { method: "PATCH", body: { name: v } });
            titleH1.textContent = v; toast("name saved");
          } catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save name"))));

  // -- description (always present; shown here + on the dashboard) ----------------
  const descInput = el("textarea", { rows: "3", placeholder: "what this routine does — a short summary shown on the dashboard and here",
    style: "width:100%;max-width:640px;resize:vertical" }, d.description || "");
  view.append(...settingsSection({ title: "Description", id: "description" },
    "a short summary of what this routine does — shown on the dashboard and here",
      descInput,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: async () => {
          const v = descInput.value.trim();
          if (!v) { toast("description can't be empty"); return; }
          try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { description: v } }); toast("description saved"); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save description"))));

  // -- tags (shared editor — every add/remove saves immediately) --------------------
  view.append(...settingsSection({ title: "Tags", id: "tags" },
    ["freeform labels for filtering on the dashboard (e.g. meta tucks a routine away by ",
     "default) — each change saves immediately"],
      tagsEditor(d.tags, async (next) => {
        await api(`/api/routines/${slug}`, { method: "PATCH", body: { tags: next } });
        toast("tags saved");
      })));

  // -- schedule -------------------------------------------------------------------
  const nextFireLine = el("div", { class: "muted mt small" },
    ...(d.next_fire ? ["next run · ", when(d.next_fire)] : []));
  // saves update the header chip + next-fire IN PLACE — never a page reload
  async function refreshHead() {
    try {
      const nd = await api(`/api/routines/${slug}`);
      chipHost.replaceChildren(runChip(nd));
      nextFireLine.replaceChildren(...(nd.next_fire ? ["next run · ", when(nd.next_fire)] : []));
    } catch { /* cosmetic refresh — the save itself already succeeded */ }
  }
  // D71: a member of a SCHEDULED lane is "lane managed" — the dropdown locks on that
  // state (linking to the lane) and a save leaves the stored schedule untouched.
  //
  // With ONE exception, which is why the editor takes a save of its own here: a lane-managed
  // routine's file can still record a cron the daemon suppresses. Clearing it decides nothing
  // about timing — the lane's clock is untouched and the routine fires exactly as before — so
  // it is not the lane's call to make; the surface's `schedule:cron` row sends the reader here
  // to make it. A manual spec is what "no cron of its own" is stored as.
  const sched = scheduleEditor(d.schedule_friendly || { frequency: "manual" }, d.server_tz,
    { catchup: d.catchup || "skip", laneManaged: d.lane_managed || null,
      onClearCron: async () => {
        try {
          await api(`/api/routines/${slug}`, { method: "PATCH",
            body: { schedule: { friendly: { frequency: "manual" } } } });
          toast("stored cron cleared — this routine goes on firing with its lane");
          refreshHead();
          refreshSurface();   // the `schedule:cron` row that sent the reader here is now closed
        } catch (err) {
          // the editor keeps the button live on a rejection, so the reader can try again
          toast(err.message, 4000, { error: true });
          throw err;
        }
      } });
  const enabledBox = el("input", { type: "checkbox", checked: d.enabled || null });
  const improveBox = el("input", { type: "checkbox", checked: d.improve !== false || null });
  view.append(...settingsSection({ title: "Schedule", id: "schedule" },
    "when this routine runs on its own — a cron-like cadence in the server's timezone, plus the "
    + "master enable switch and whether the improver visits it. A routine in a SCHEDULED lane "
    + "fires on the lane's clock instead; the cadence below is then the lane's to set, leaving "
    + "one change here — clearing a cron of its own that the lane suppresses.",
      sched.node,
      el("label", { class: "row mt", style: "gap:8px" }, enabledBox, "enabled"),
      el("label", { class: "row mt", style: "gap:8px" }, improveBox,
        el("span", {}, "include in improvement — the routine-improver meta routine visits this routine (on by default)")),
      el("div", { class: "row mt" }, el("button", {
        class: "btn primary",
        onclick: async () => {
          try {
            await api(`/api/routines/${slug}`, { method: "PATCH",
              body: { enabled: enabledBox.checked, improve: improveBox.checked,
                      // lane-managed: EDITING the cadence stays the lane's business, so this
                      // save sends no schedule at all. Clearing a suppressed cron is the other
                      // act — it saves from its own control above, on its own PATCH.
                      ...(d.lane_managed ? {}
                        : { schedule: { friendly: sched.value(), catchup: sched.catchup() } }) } });
            // a cadence moves `schedule:none`; the enable switch takes every schedule row
            // with it, because a routine switched off already says it does not run
            toast("schedule saved"); refreshHead(); refreshSurface();
          } catch (err) { toast(err.message, 4000, { error: true }); }
        },
      }, "save schedule")),
      nextFireLine));

  // -- triggers: event-driven fires alongside cron (webhook URLs, coalescing) -------
  view.append(...settingsSection({ title: "Triggers", id: "triggers" },
    "event-driven fires that run this routine alongside its cron schedule — each webhook trigger "
    + "gives a URL that starts a run when called (with coalescing so a burst fires once).",
    triggersCard(slug, d.triggers || [])));

  // -- schedule once: a one-shot future run that fires once then auto-removes --------
  view.append(...settingsSection({ title: "Schedule once", id: "schedule-once" },
    "arm a single future run at a specific time — it fires exactly once, then removes itself "
    + "(the recurring schedule above is unaffected).",
    scheduleOnceCard(slug)));

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
  templatePanel(tplHost, slug, d, { onApplied: refreshSurface });

  // -- domain: the shared surface, right after the template it reads next to ------------------
  // The two answer the same question — where does this routine's config come from? — in
  // opposite ways: a template COPIES once and the copy is then this routine's own, a domain
  // LAYERS under this file at every load and can be left again.
  domainSection(view, slug, d);

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
      } catch (err) { toast(err.message, 4000, { error: true }); }
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
    const lib = await api("/api/library").catch(() => ({ rules: [] }));
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
  const surfacePanel = surfaceView(surfaceHost, slug, d.surface);

  /** Re-read the surface and repaint every reader of it. Called by each save on this page that
   *  can move a row — permissions, rules, grants, roots, machines, connections, the schedule,
   *  the goal — because the reader who performed a fix is looking at the diagnosis that sent
   *  them, whose button now aims at a control that panel's repaint has removed.
   *
   *  ONE request, then three repaints: this section's panel, the strip above the hero (which
   *  routine.js owns, so it hands the repaint down), and `d.surface`, which is what the ability
   *  cards are rebuilt from. A read that FAILS hands each reader a null, which is its
   *  instruction to read for itself and say so if it cannot — showing the pre-save answer as
   *  though the save never happened is the one outcome worth two extra requests to avoid. */
  let surfaceRead = 0;                  // two quick saves: the older answer must not land last
  async function refreshSurface() {
    const mine = ++surfaceRead;
    let next = null;
    try { next = await api(`/api/routines/${slug}/surface`); }
    catch { /* each reader repaints from its own read, or renders unavailable */ }
    if (mine !== surfaceRead) return;
    d.surface = next;
    surfacePanel.refresh(next);
    repaintSetup?.(next);
  }

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
        onclick: async () => {
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
          try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { budgets } });
            toast("budgets saved"); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save budgets"))));

  // -- retention: how many finished run dirs to keep ------------------------------
  const keepRunsIn = el("input", { type: "number", min: "1", value: String(d.keep_runs ?? 30), style: "width:110px" });
  view.append(...settingsSection({ title: "Retention", id: "retention" },
    ["how many finished run directories to keep — older ones are pruned (transcripts gzip first). ",
     "The durable usage stream (spend, health) survives pruning."],
      el("div", { class: "row" }, keepRunsIn, el("span", {}, "runs kept"),
        el("button", { class: "btn primary", onclick: async () => {
          const n = parseInt(keepRunsIn.value, 10);
          if (!Number.isFinite(n) || n < 1) { toast("keep at least 1 run"); return; }
          try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { keep_runs: n } });
            toast("retention saved"); }
          catch (err) { toast(err.message, 4000, { error: true }); }
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
      el("div", { class: "row mt" }, el("button", { class: "btn primary", onclick: async () => {
        try {
          await api(`/api/routines/${slug}`, { method: "PATCH",
            body: { fs_read_roots: readRoots.value(), fs_write_roots: writeRoots.value() } });
          toast("filesystem roots saved");
          refreshSurface();   // every fs-read:/fs-write: row is a containment test on these
        } catch (err) { toast(err.message, 4000, { error: true }); }
      } }, "save roots"))));

  // -- models (per routine: main / tool_call / uncensored; children run main by default,
  //    a spawn/subtask call may override per child) ------------------------------
  const MODEL_KINDS = [["main", "the orchestrator loop (children inherit it by default)"],
                       ["tool_call", "the llm action"],
                       ["uncensored", "a refused llm call is referred here (opt-in)"]];
  const catalog = d.catalog || [];      // catalog model names (see Settings → Models)
  const sysM = d.system_model;          // the system model's catalog name (or null)
  const modelSelects = {};
  const modelRows = MODEL_KINDS.map(([kind, desc]) => {
    const cur = (d.models && d.models[kind]) || "";   // a catalog model NAME, or "" = fallback
    const sel = el("select", {}, [
      el("option", { value: "" }, sysM ? `— system default (${sysM}) —` : "— system default —"),
      ...catalog.map((n) => el("option", { value: n }, n))]);
    sel.value = cur || "";
    modelSelects[kind] = sel;
    return el("div", { class: "row", style: "margin:5px 0" },
      el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, kind),
      el("span", { class: "muted small", style: "min-width:150px" }, desc),
      sel);
  });
  const refMonth = d.spend?.current?.referrals || 0;
  // Deliberation: how much thinking lands on paper (the say/notes contract). Saved on
  // release — the next run composes with the new level (a LIVE run is re-leveled from
  // the run view, control.json-scoped).
  const delib = deliberationControl(d.deliberation || "standard", {
    onCommit: async (level) => {
      try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { deliberation: level } });
        toast(`deliberation: ${level} — applies from the next run`); }
      catch (err) { toast(err.message, 4000, { error: true }); }
    },
  });
  view.append(...settingsSection({ title: "Models", id: "models" },
    catalog.length
      ? "which catalog model this routine uses for each role — leave on system default to fall back to the system model"
      : "add a model in Settings first",
      ...modelRows,
      el("div", { class: "row mt", style: "align-items:flex-start" },
        el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, "deliberation"),
        el("span", { class: "muted small", style: "min-width:150px" },
          "how much thinking lands on paper"),
        delib.node),
      d.referrals_total
        ? el("div", { class: "muted small mt",
            title: "turns or llm calls the main/tool model refused and the uncensored model answered instead (from the durable usage stream)" },
            `↪ uncensored referrals: ${d.referrals_total} total` + (refMonth ? ` · ${refMonth} this month` : ""))
        : null,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: async () => {
          const models = {};
          for (const [kind, sel] of Object.entries(modelSelects))
            if (sel.value) models[kind] = sel.value;
          try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { models } });
            toast("models saved"); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save models"))));

  // -- connections: bind an OAuth account per provider (Settings → Connections) --------
  // Shared card (components/connections.js) — the conversation header uses the same one.
  view.append(...settingsSection({ title: "Connections", id: "connections" },
    "bind an OAuth account per provider so this routine's util calls act as that account — "
    + "a provider left unbound reaches the run as no account at all. Manage the accounts "
    + "themselves in Settings → Connections.",
    connectionsCard(d.connections || {}, {
      onSave: async (connections) => {
        await api(`/api/routines/${slug}`, { method: "PATCH", body: { connections } });
        refreshSurface();   // a `connection:` row a held rule expects is bound or unbound here
      },
    })));

  // -- own secrets: this routine's private store (D103) ---------------------------------------
  view.append(...settingsSection({ title: "Own secrets", id: "own-secrets" },
    ["the private half of the two-scope store: credentials belonging to THIS routine, as ",
     "opposed to the shared-store names it is exposed to below."],
    routineSecretsCard(slug)));

  // -- grant decisions: secret exposure (D39) + declined-access tombstones ---------------------
  // Both live in routine.yaml `grants:` (entity ids, entities.py): `secret:<NAME>` rows are
  // the exposure map; a FALSE row of any other class is a deny-forever tombstone an access
  // request left behind (the run stops asking). Saving REPLACES the whole mapping, so the
  // two editors below always write their rows together.
  const secBox = el("div", {}, skeleton(["50%"]));
  view.append(...settingsSection({ title: "Secret exposure", id: "secret-exposure" },
    ["which of the SHARED store's secrets this routine's util calls may receive. An undecided ",
     "secret is asked about the FIRST time a util call declares it — a blocking access request, ",
     "whose answer is remembered here. Manage the secrets themselves in ",
     el("a", { href: "#/settings?section=secrets" }, "Settings → Secrets"), "."],
    secBox));
  const declinedBox = el("div", {}, skeleton(["50%"]));
  view.append(...settingsSection({ title: "Declined access", id: "declined-access" },
    ["access this routine's requests were declined FOREVER — it no longer asks for these; the ",
     "engine refuses them. Removing a row returns the entity to undecided (requestable again)."],
    declinedBox));
  // F193: a grant decided elsewhere (a Decisions-page approval) lands in
  // routine.yaml while this page is open — the panel refetches BOTH the store and the
  // routine's CURRENT grants instead of rendering the page-load snapshot forever.
  const loadSecrets = async () => {
    let sec, grants;
    try {
      sec = await api("/api/settings/secrets");
      grants = (await api(`/api/routines/${slug}`)).grants || {};
    } catch (err) { secBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    const secretRows = Object.fromEntries(Object.entries(grants)
      .filter(([k]) => k.startsWith("secret:")).map(([k, v]) => [k.slice("secret:".length), v]));
    const otherRows = Object.fromEntries(Object.entries(grants)
      .filter(([k]) => !k.startsWith("secret:")));
    const saveGrants = async (updated, note) => {
      try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { grants: updated } });
        // an exposure decision settles a `secret:` row; clearing a tombstone reopens one
        toast(note); loadSecrets(); refreshSurface(); }
      catch (err) { toast(err.message, 4000, { error: true }); }
    };
    const names = [...new Set([...(sec.keys || []), ...Object.keys(secretRows)])].sort();
    // The panel's copy is the SECTION DESCRIPTION above, so it is on screen from the first
    // paint — before this fetch lands, still there when the store holds nothing.
    secBox.replaceChildren();
    if (!names.length) {
      secBox.append(el("div", { class: "muted small" }, "no secrets in the store yet"));
    }
    const secSelects = {};
    for (const name of names) {
      const sel = el("select", {}, [
        el("option", { value: "" }, "ask on first use"),
        el("option", { value: "true" }, "expose"),
        el("option", { value: "false" }, "withhold")]);
      sel.value = name in secretRows ? String(!!secretRows[name]) : "";
      secSelects[name] = sel;
      secBox.append(el("div", { class: "row", style: "margin:5px 0", "data-secret-row": name },
        el("code", { class: "small", style: "min-width:240px" }, name), sel,
        (sec.keys || []).includes(name) ? null
          : el("span", { class: "muted small" }, "not in the store (stale entry)")));
    }
    if (names.length) {
      secBox.append(el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: () => {
          const updated = { ...otherRows };
          for (const [name, sel] of Object.entries(secSelects))
            if (sel.value) updated[`secret:${name}`] = sel.value === "true";
          saveGrants(updated, "secret exposure saved");
        } }, "save secret exposure")));
    }
    declinedBox.replaceChildren();
    const declined = Object.keys(otherRows).filter((k) => otherRows[k] === false).sort();
    if (!declined.length) {
      declinedBox.append(el("div", { class: "muted small" }, "nothing declined"));
    }
    for (const eid of declined) {
      declinedBox.append(el("div", { class: "row", style: "margin:5px 0", "data-declined-row": eid },
        el("code", { class: "small", style: "min-width:240px" }, eid),
        el("button", { class: "btn small", title: "make it requestable again",
          onclick: () => {
            const updated = { ...otherRows };
            delete updated[eid];
            for (const [name, v] of Object.entries(secretRows)) updated[`secret:${name}`] = v;
            saveGrants(updated, "declined entry removed — requestable again");
          } }, "remove")));
    }
  };
  loadSecrets();
  // Refetch when a decision lands (an access request resolves as a question answer). The
  // web layer persists a forever-decision BEFORE publishing the answer event, so one
  // refetch suffices; the listener unhooks itself once the panel has left the DOM (SPA
  // remount). F193 heritage: never render the page-load snapshot forever.
  const onSecretsBus = (e) => {
    if (!secBox.isConnected) { window.removeEventListener("rsched-bus", onSecretsBus); return; }
    if (e.detail?.event === "question_answered" && e.detail.routine === slug) {
      loadSecrets();
      refreshSurface();   // a forever-decision is a grant, so it settles a row up here too
    }
  };
  window.addEventListener("rsched-bus", onSecretsBus);

  // -- machines: the shared binding card (components/machines.js) — D102: the conversation
  // header mounts the same card, so both surfaces bind catalog machines identically --------
  view.append(...settingsSection({ title: "Machines", id: "machines" },
    ["which boxes from the instance's catalog this routine may reach over SSH. A binding is a ",
     "RESOURCE, not a permission: it says which machines are in reach, while the ",
     el("code", {}, "remote-machines"), " ability above is what lets a run act on one. Takes ",
     "effect at the next run."],
    machinesCard(d.machine_catalog || [], d.machines || [], {
      onSave: async (machines) => {
        await api(`/api/routines/${slug}`, { method: "PATCH", body: { machines } });
        refreshSurface();   // a `machine:` row a held doc expects is bound or unbound here
      },
    })));

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
  return { refreshHead, refreshSurface };
}
