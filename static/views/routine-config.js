// Routine config sections (Name .. Origin): every user-editable panel of the routine
// page - rename, description, tags, schedule, triggers, schedule-once, permissions,
// practice modules, budgets, retention, fs roots, models + deliberation, connections,
// machines, and origin. Split from routine.js; returns { refreshHead } (the in-place
// header/next-fire refresher the run-lifecycle bus handler calls).

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
import { triggersCard } from "/static/components/triggers.js";

const INHERIT_LABEL = {
  permissions: "permissions", capabilities: "capabilities", rules: "general rules",
  machines: "machines", tags: "tags", models: "models", connections: "connections",
  grants: "secret grants", budgets: "budgets",
  fs_read_roots: "readable roots", fs_write_roots: "writable roots",
};

/** D82: a banner naming what this routine got from its group, so an inherited value is never
 *  mistaken for one set here. The panels below stay as they are — they show the EFFECTIVE
 *  config, which is what the run actually gets. */
function inheritedNote(d) {
  const fields = Object.keys(d.inherited || {});
  if (!fields.length) return null;
  return el("div", { class: "panel mt", "data-inherited-note": "" },
    el("div", { class: "small" },
      el("b", {}, "Some settings below come from the group"),
      d.inherited_from ? ` “${d.inherited_from}”` : "", "."),
    el("div", { class: "muted small", style: "margin-top:4px" },
      fields.map((f) => `${INHERIT_LABEL[f] || f} (${d.inherited[f]})`).join(" · ")),
    el("div", { class: "muted small", style: "margin-top:4px" },
      "The panels show the EFFECTIVE config — what this routine actually runs with. Editing "
      + "here changes only this routine's own value, which always wins; change the shared part "
      + "in the group's editor on the Routines page."));
}

export function renderConfigSections(view, d, { slug, titleH1, chipHost, runChip }) {
  const note = inheritedNote(d);
  if (note) view.append(note);
  // -- name (rename; the header + dashboard show it — slug stays the identity) ------
  const nameInput = el("input", { type: "text", value: d.name || slug, placeholder: "routine name",
    style: "width:100%;max-width:420px" });
  view.append(...settingsSection("Name",
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
  const descInput = el("input", { type: "text", value: d.description || "", placeholder: "one-line description",
    style: "width:100%;max-width:640px" });
  view.append(...settingsSection("Description",
    "a one-line summary of what this routine does — shown on the dashboard and here",
      descInput,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: async () => {
          const v = descInput.value.trim();
          if (!v) { toast("description can't be empty"); return; }
          try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { description: v } }); toast("description saved"); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save description"))));

  // -- tags (shared editor — every add/remove saves immediately) --------------------
  view.append(...settingsSection("Tags",
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
  // D71: a member of a SCHEDULED group is "group managed" — the dropdown locks on that
  // state (linking to the group) and a save leaves the stored schedule untouched.
  const sched = scheduleEditor(d.schedule_friendly || { frequency: "manual" }, d.server_tz,
    { catchup: d.catchup || "skip", groupManaged: d.group_managed || null });
  const enabledBox = el("input", { type: "checkbox", checked: d.enabled || null });
  const improveBox = el("input", { type: "checkbox", checked: d.improve !== false || null });
  view.append(...settingsSection("Schedule",
    "when this routine runs on its own — a cron-like cadence in the server's timezone, plus the "
    + "master enable switch and whether the improver visits it.",
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
                      // group-managed: the schedule stays the group's business — send none
                      ...(d.group_managed ? {}
                        : { schedule: { friendly: sched.value(), catchup: sched.catchup() } }) } });
            toast("schedule saved"); refreshHead();
          } catch (err) { toast(err.message, 4000, { error: true }); }
        },
      }, "save schedule")),
      nextFireLine));

  // -- triggers: event-driven fires alongside cron (webhook URLs, coalescing) -------
  view.append(...settingsSection("Triggers",
    "event-driven fires that run this routine alongside its cron schedule — each webhook trigger "
    + "gives a URL that starts a run when called (with coalescing so a burst fires once).",
    triggersCard(slug, d.triggers || [])));

  // -- schedule once: a one-shot future run that fires once then auto-removes --------
  view.append(...settingsSection("Schedule once",
    "arm a single future run at a specific time — it fires exactly once, then removes itself "
    + "(the recurring schedule above is unaffected).",
    scheduleOnceCard(slug)));

  // -- settings template: the named starting point the panels below layer over ------------
  // The panel itself lives in components/template-panel.js: picking a template is one control,
  // but READING one — what it supplies, what this routine drops from it, what is set here —
  // is the part that was missing — and it is too much to inline here.
  const tplHost = el("div", {});
  view.append(...settingsSection("Settings template",
    ["a named starting point for this routine's whole conduct surface — its conduct docs, ",
     "capabilities, general rules and grants. It layers UNDER this routine's own settings, so ",
     "everything below stays editable and anything you set here wins. Nothing is copied: ",
     "editing the template in the library reaches every routine that adopted it."],
    tplHost));
  templatePanel(tplHost, slug, d);

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
        permHost.replaceChildren(buildPermPanel(nd.permissions, nd.capabilities));
      } catch (err) { toast(err.message, 4000, { error: true }); }
    },
  }).node;
  permHost.append(buildPermPanel(d.permissions, d.capabilities));
  view.append(...settingsSection("Permissions & capabilities",
    ["what this routine is ALLOWED to do — enforced by the engine on every action. Only you can ",
     "change either column; the routine can never grant itself anything. Takes effect at the next run."],
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
      },
    }).node;
  };
  buildRulePanel(d).then((n) => ruleHost.replaceChildren(n));
  view.append(...settingsSection("General rules",
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
  view.append(...settingsSection("Effective surface",
    ["every dependency this routine's setup resolves to — secrets, roots, machines, ",
     "connections, reserved utils — with the conduct doc or util that declares each one. ",
     "Read-only: each row is edited in the panel that owns it; a second place to change ",
     "one value is a second place for it to be wrong."],
    surfaceHost));
  // `d.surface` is the fetch routine.js already made for the strip and the ability cards —
  // one read feeds all three readers rather than three requests for one answer.
  surfaceView(surfaceHost, slug, d.surface);

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
  view.append(...settingsSection("Goal",
    ["what DONE means for one run, in your own words — conditions the run must account for in ",
     "its finish summary (`[s1] met — …`), combined with all/any and optionally scoped to a ",
     "stage. Reported, never enforced: the engine judges no semantics, it makes them impossible ",
     "to ignore. Without any, a run is bounded only by its budgets."],
    goalHost));
  // showStage: a per-stage condition is a ROUTINE concept — a conversation has no stages
  createStopping(goalHost, { url: `/api/routines/${slug}/stopping`, showStage: true });

  view.append(...settingsSection("Budgets",
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
  view.append(...settingsSection("Retention",
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
  view.append(...settingsSection("Filesystem roots",
    ["extra directories this routine may access beyond its own dir — browse to each. ",
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
  view.append(...settingsSection("Models",
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
  view.append(...settingsSection("Connections",
    "bind an OAuth account per provider so this routine's util calls act as that account — "
    + "manage the accounts themselves in Settings → Connections.",
    connectionsCard(d.connections || {}, {
      onSave: (connections) => api(`/api/routines/${slug}`,
        { method: "PATCH", body: { connections } }),
    })));

  // -- own secrets: this routine's private store (D103) ---------------------------------------
  view.append(...settingsSection("Own secrets", "", routineSecretsCard(slug)));

  // -- grant decisions: secret exposure (D39) + declined-access tombstones ---------------------
  // Both live in routine.yaml `grants:` (entity ids, entities.py): `secret:<NAME>` rows are
  // the exposure map; a FALSE row of any other class is a deny-forever tombstone an access
  // request left behind (the run stops asking). Saving REPLACES the whole mapping, so the
  // two editors below always write their rows together.
  const secBox = el("div", {}, skeleton(["50%"]));
  view.append(...settingsSection("Secret exposure", "", secBox));
  const declinedBox = el("div", {}, skeleton(["50%"]));
  view.append(...settingsSection("Declined access", "", declinedBox));
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
        toast(note); loadSecrets(); }
      catch (err) { toast(err.message, 4000, { error: true }); }
    };
    const names = [...new Set([...(sec.keys || []), ...Object.keys(secretRows)])].sort();
    secBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:8px" },
      "Which store secrets this routine's util calls may receive. An undecided secret is asked ",
      "about the FIRST time a util call declares it (a blocking access request, remembered ",
      "here). Manage the secrets themselves in ",
      el("a", { href: "#/settings?section=secrets" }, "Settings → Secrets"), "."));
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
    declinedBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:8px" },
      "Access this routine's requests were declined FOREVER — it no longer asks for these. ",
      "Removing a row returns the entity to undecided (requestable again)."));
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
    if (e.detail?.event === "question_answered" && e.detail.routine === slug) loadSecrets();
  };
  window.addEventListener("rsched-bus", onSecretsBus);

  // -- machines: the shared binding card (components/machines.js) — D102: the conversation
  // header mounts the same card, so both surfaces bind catalog machines identically --------
  view.append(...settingsSection("Machines", "",
    machinesCard(d.machine_catalog || [], d.machines || [], {
      onSave: (machines) => api(`/api/routines/${slug}`, { method: "PATCH", body: { machines } }),
    })));

  // -- origin: the library pattern this routine was generated from (provenance only) ----------
  const wf = d.workflow_ref || {};
  view.append(...settingsSection("Origin", "",
      el("span", { class: "ref-tag" }, wf.slug || "hand-authored"),
      el("span", { class: "muted small", style: "margin-left:10px" },
        wf.slug
          ? (wf.in_library
             ? "the library pattern this routine was generated from — its recipe is the routine's OWN now (edit it in the Recipe section below)"
             : "its origin pattern is no longer in this library — the recipe is the routine's OWN (edit it in the Recipe section below)")
          : "written directly, not generated from a library pattern")));
  return { refreshHead };
}
