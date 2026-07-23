// Routine config sections (Name .. Origin): every user-editable panel of the routine
// page - rename, description, tags, schedule, triggers, schedule-once, permissions,
// practice modules, budgets, retention, fs roots, models + deliberation, connections,
// machines, and origin. Split from routine.js; returns { refreshHead } (the in-place
// header/next-fire refresher the run-lifecycle bus handler calls).

import { BUDGET_FIELDS, UNLIMITED_BUDGETS } from "/static/components/budgetfields.js";
import { api } from "/static/api.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { el, skeleton, toast, when } from "/static/util.js";
import { permissionsPanel } from "/static/components/permissions.js";
import { rootsEditor } from "/static/components/fsroots.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { scheduleOnceCard } from "/static/components/schedule-once.js";
import { tagsEditor } from "/static/components/tags.js";
import { traitPicker } from "/static/components/traitpicker.js";
import { triggersCard } from "/static/components/triggers.js";

export function renderConfigSections(view, d, { slug, titleH1, chipHost, runChip }) {
  // -- name (rename; the header + dashboard show it — slug stays the identity) ------
  const nameInput = el("input", { type: "text", value: d.name || slug, placeholder: "routine name",
    style: "width:100%;max-width:420px" });
  view.append(el("h2", {}, "Name"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "the display name (the folder ", el("span", { class: "ref-tag" }, slug), " stays the identity)"),
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
  view.append(el("h2", {}, "Description"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "a one-line summary of what this routine does — shown on the dashboard and here"),
      descInput,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: async () => {
          const v = descInput.value.trim();
          if (!v) { toast("description can't be empty"); return; }
          try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { description: v } }); toast("description saved"); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save description"))));

  // -- tags (shared editor — every add/remove saves immediately) --------------------
  view.append(el("h2", {}, "Tags"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "freeform labels for filtering on the dashboard (e.g. meta tucks a routine away by ",
        "default) — each change saves immediately"),
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
  const sched = scheduleEditor(d.schedule_friendly || { frequency: "manual" }, d.server_tz,
    { catchup: d.catchup || "skip" });
  const enabledBox = el("input", { type: "checkbox", checked: d.enabled || null });
  const improveBox = el("input", { type: "checkbox", checked: d.improve !== false || null });
  view.append(el("h2", {}, "Schedule"),
    el("div", { class: "panel" }, sched.node,
      el("label", { class: "row mt", style: "gap:8px" }, enabledBox, "enabled"),
      el("label", { class: "row mt", style: "gap:8px" }, improveBox,
        el("span", {}, "include in improvement — the routine-improver meta routine visits this routine (on by default)")),
      el("div", { class: "row mt" }, el("button", {
        class: "btn primary",
        onclick: async () => {
          try {
            await api(`/api/routines/${slug}`, { method: "PATCH",
              body: { enabled: enabledBox.checked, improve: improveBox.checked,
                      schedule: { friendly: sched.value(), catchup: sched.catchup() } } });
            toast("schedule saved"); refreshHead();
          } catch (err) { toast(err.message, 4000, { error: true }); }
        },
      }, "save schedule")),
      nextFireLine));

  // -- triggers: event-driven fires alongside cron (webhook URLs, coalescing) -------
  view.append(el("h2", {}, "Triggers"),
    triggersCard(slug, d.triggers || [], { protected: !!d.protected }));

  // -- schedule once: a one-shot future run that fires once then auto-removes --------
  view.append(el("h2", {}, "Schedule once"),
    scheduleOnceCard(slug, { protected: !!d.protected }));

  // -- permissions: conduct docs + machine-enforced capabilities (user-only) --------
  // The server re-applies the activation cascade on save, so the panel re-renders from a
  // fresh detail read IN PLACE — the old full page reload is gone.
  const permHost = el("div", {});
  const buildPermPanel = (perms, caps) => permissionsPanel(perms, caps, {
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
  view.append(el("h2", {}, "Permissions & capabilities"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:10px" },
        "what this routine is ALLOWED to do — enforced by the engine on every action. Only you can ",
        "change either column; the routine can never grant itself anything. Takes effect at the next run."),
      permHost));

  // -- practice modules (traits the routine holds; the traits/ dir IS the state) -----
  const traitHost = el("div", {});
  const buildTraitPanel = async (detail) => {
    const lib = await api("/api/library").catch(() => ({ traits: [] }));
    return traitPicker(lib.traits || [], detail.traits || [], {
      live: !!detail.active_run,
      onSave: async (payload) => {
        await api(`/api/routines/${slug}/traits`, { method: "POST", body: payload });
        const nd = await api(`/api/routines/${slug}`);
        traitHost.replaceChildren(await buildTraitPanel(nd));
      },
    }).node;
  };
  buildTraitPanel(d).then((n) => traitHost.replaceChildren(n));
  view.append(el("h2", {}, "Practice modules"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:10px" },
        "the standing practices this routine reads before the situations they govern. Added ",
        "modules are copied in verbatim and become the routine's own files; an addition reaches ",
        "a run already in flight, a removal takes effect at the next run. The routine can ",
        "CONSULT an unheld module for one run (read_trait) but never change this set."),
      traitHost));

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
  view.append(el("h2", {}, "Budgets"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "hard per-run ceilings, checked at every turn — the run is told at 85% so it can wind down ",
        "deliberately. Resources, not permissions."),
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
  view.append(el("h2", {}, "Retention"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "how many finished run directories to keep — older ones are pruned (transcripts gzip first). ",
        "The durable usage stream (spend, health) survives pruning."),
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
  view.append(el("h2", {}, "Filesystem roots"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:10px" },
        "extra directories this routine may access beyond its own dir — browse to each. ",
        el("strong", {}, "Write roots are powerful"), ": a write root that covers this routine's own ",
        "directory unlocks editing its OWN recipe (main.md / stages / traits / tuning.yaml) — the same ",
        "lever the routine-improver holds. routine.yaml stays sealed regardless. Takes effect next run."),
      el("div", { class: "field" }, el("span", {}, "read roots"), readRoots.node),
      el("div", { class: "field mt" }, el("span", {}, "write roots"), writeRoots.node),
      el("div", { class: "row mt" }, el("button", { class: "btn primary", onclick: async () => {
        try {
          await api(`/api/routines/${slug}`, { method: "PATCH",
            body: { fs_read_roots: readRoots.value(), fs_write_roots: writeRoots.value() } });
          toast("filesystem roots saved");
        } catch (err) { toast(err.message, 4000, { error: true }); }
      } }, "save roots"))));

  // -- models (per routine: main / subroutine / tool_call / uncensored) ----------
  const MODEL_KINDS = [["main", "the orchestrator loop"], ["subroutine", "spawned sub-workflows"],
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
  view.append(el("h2", {}, "Models"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        catalog.length
          ? "which catalog model this routine uses for each role — leave on system default to fall back to the system model"
          : "add a model in Settings first"),
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
  const connSelects = {};
  const connBox = el("div", { class: "panel" }, skeleton(["50%"]));
  view.append(el("h2", {}, "Connections"), connBox);
  api("/api/settings/oauth").then((oauth) => {
    connBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:8px" },
      "Bind an OAuth account per provider — its access token is injected into utils that declare it ",
      "(e.g. NOTION_ACCESS_TOKEN). Connect accounts in ",
      el("a", { href: "#/settings?section=connections" }, "Settings → Connections"), "."));
    const byProvider = {};
    for (const c of (oauth.connections || [])) (byProvider[c.provider] ||= []).push(c.account);
    const bound = d.connections || {};
    for (const p of (oauth.providers || [])) {
      const accounts = byProvider[p.id] || [];
      const sel = el("select", {}, [el("option", { value: "" }, "— none —"),
        ...accounts.map((a) => el("option", { value: a }, a))]);
      sel.value = bound[p.id] || "";
      connSelects[p.id] = sel;
      connBox.append(el("div", { class: "row", style: "margin:5px 0", "data-conn-row": p.id },
        el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, p.name),
        accounts.length ? sel
          : el("span", { class: "muted small" }, "no connected accounts — connect one in Settings")));
    }
    connBox.append(el("div", { class: "row mt" }, el("button", { class: "btn primary",
      onclick: async () => {
        const connections = {};
        for (const [pid, sel] of Object.entries(connSelects)) if (sel.value) connections[pid] = sel.value;
        try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { connections } }); toast("connections saved"); }
        catch (err) { toast(err.message, 4000, { error: true }); }
      } }, "save connections")));
  }).catch((err) => connBox.replaceChildren(el("div", { class: "muted" }, err.message)));

  // -- secret exposure: which store secrets this routine's util calls may receive (D39) --------
  const secBox = el("div", { class: "panel" }, skeleton(["50%"]));
  view.append(el("h2", {}, "Secret exposure"), secBox);
  api("/api/settings/secrets").then((sec) => {
    const grants = d.secret_grants || {};
    const names = [...new Set([...(sec.keys || []), ...Object.keys(grants)])].sort();
    secBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:8px" },
      "Which store secrets this routine's util calls may receive. An undecided secret is asked ",
      "about the FIRST time a util call declares it (a blocking question, remembered here). ",
      "Manage the secrets themselves in ",
      el("a", { href: "#/settings?section=secrets" }, "Settings → Secrets"), "."));
    if (!names.length) {
      secBox.append(el("div", { class: "muted small" }, "no secrets in the store yet"));
      return;
    }
    const secSelects = {};
    for (const name of names) {
      const sel = el("select", {}, [
        el("option", { value: "" }, "ask on first use"),
        el("option", { value: "true" }, "expose"),
        el("option", { value: "false" }, "withhold")]);
      sel.value = name in grants ? String(!!grants[name]) : "";
      secSelects[name] = sel;
      secBox.append(el("div", { class: "row", style: "margin:5px 0", "data-secret-row": name },
        el("code", { class: "small", style: "min-width:240px" }, name), sel,
        (sec.keys || []).includes(name) ? null
          : el("span", { class: "muted small" }, "not in the store (stale entry)")));
    }
    secBox.append(el("div", { class: "row mt" }, el("button", { class: "btn primary",
      onclick: async () => {
        const secret_grants = {};
        for (const [name, sel] of Object.entries(secSelects))
          if (sel.value) secret_grants[name] = sel.value === "true";
        try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { secret_grants } });
          toast("secret exposure saved"); }
        catch (err) { toast(err.message, 4000, { error: true }); }
      } }, "save secret exposure")));
  }).catch((err) => secBox.replaceChildren(el("div", { class: "muted" }, err.message)));

  // -- machines: bind the remote SSH hosts this routine may act on (Settings → Machines) --------
  const catalogM = d.machine_catalog || [];   // instance-wide machine catalog (name + summary)
  const boundM = new Set(d.machines || []);    // names this routine currently binds
  const machChecks = {};
  view.append(el("h2", {}, "Machines"));
  const machPanel = el("div", { class: "panel" },
    el("div", { class: "muted small", style: "margin-bottom:8px" },
      "Remote machines this routine may act on over SSH (needs the ",
      el("code", {}, "remote-machines"), " permission + the ", el("code", {}, "remote"),
      " util). Add machines in ",
      el("a", { href: "#/settings?section=machines" }, "Settings → Machines"), "."));
  if (!catalogM.length) {
    machPanel.append(el("div", { class: "muted small" }, "no machines in the catalog yet"));
  } else {
    for (const m of catalogM) {
      const cb = el("input", { type: "checkbox" });
      if (boundM.has(m.name)) cb.checked = true;
      machChecks[m.name] = cb;
      const meta = m.description || `${m.user}@${m.host}`;
      const tags = (m.tags || []).length ? ` [${m.tags.join(", ")}]` : "";
      machPanel.append(el("label", { class: "row", style: "margin:5px 0;gap:8px;cursor:pointer" },
        cb, el("span", { style: "font-weight:600;min-width:110px" }, m.name),
        el("span", { class: "muted small" }, meta + tags)));
    }
    // A binding to a machine no longer in the catalog: keep it visible so it can be cleared.
    for (const name of boundM) if (!catalogM.some((m) => m.name === name)) {
      const cb = el("input", { type: "checkbox", checked: "" });
      machChecks[name] = cb;
      machPanel.append(el("label", { class: "row", style: "margin:5px 0;gap:8px;cursor:pointer" },
        cb, el("span", { style: "font-weight:600;min-width:110px" }, name),
        el("span", { class: "small", style: "color:var(--warn)" }, "not in the catalog — uncheck to clear")));
    }
    machPanel.append(el("div", { class: "row mt" }, el("button", { class: "btn primary",
      onclick: async () => {
        const machines = Object.entries(machChecks).filter(([, cb]) => cb.checked).map(([n]) => n);
        try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { machines } }); toast("machines saved"); }
        catch (err) { toast(err.message, 4000, { error: true }); }
      } }, "save machines")));
  }
  view.append(machPanel);

  // -- origin: the library pattern this routine was generated from (provenance only) ----------
  const wf = d.workflow_ref || {};
  view.append(el("h2", {}, "Origin"),
    el("div", { class: "panel" },
      el("span", { class: "ref-tag" }, wf.slug || "hand-authored"),
      el("span", { class: "muted small", style: "margin-left:10px" },
        wf.slug
          ? (wf.in_library
             ? "the library pattern this routine was generated from — its recipe is the routine's OWN now (edit it in the Recipe section below)"
             : "its origin pattern is no longer in this library — the recipe is the routine's OWN (edit it in the Recipe section below)")
          : "written directly, not generated from a library pattern")));
  return { refreshHead };
}
