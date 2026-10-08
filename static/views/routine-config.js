// Routine SETTINGS — one form over every setting, led by the routine's pattern, saved by one
// accept.
//
// The page reads the routine's settings as ONE document (GET …/settings) into a draft
// (components/settings-form.js); every control below edits that draft, a changed value is
// marked where it sits and in the group that folds it away, and the accept bar at the foot of
// the viewport sends every change the person kept in one request. What stays live are the
// controls that are ACTIONS rather than settings: arming a one-shot, the recipe file editor,
// the routine's own secret store, clearing a cron a lane already overrides.
//
// The settings are seven groups, each its own module and each one labelled fold of the page:
//   Schedule & gate    (routine-config-schedule.js)  — open on arrival
//   Goal               (routine-config-goal.js)      — open on arrival
//   Abilities          (routine-config-abilities.js)
//   Secrets & access   (routine-config-access.js)
//   Limits & reach     (routine-config-limits.js)
//   Models             (routine-config-models.js)
//   Identity & recipe  (routine-config-identity.js)
// Inside each, the rarely needed sections sit behind the group's own "more" menu.
//
// Every section is built by the shared settingsSection primitive in its { title, id } form, so
// each heading carries a stable `sec-<id>` anchor: the address the setup-check strip's remedies
// land on (components/surface-view.js). Ids are a contract — reword a heading freely, keep its id.
//
// This file owns the one re-read of the SETUP SURFACE (`refreshSurface`): after an accept, the
// strip above the hero, the ability cards and the effective-surface panel all read one answer.

import { api } from "/static/api.js";
import { el } from "/static/util.js";
import { acceptBar } from "/static/components/accept-bar.js";
import { draftBanner, patternBar } from "/static/components/pattern-bar.js";
import { createSettingsForm, loadSettings } from "/static/components/settings-form.js";
import { reveal } from "/static/components/settings-field.js";
import { abilitiesGroup } from "/static/views/routine-config-abilities.js";
import { accessGroup } from "/static/views/routine-config-access.js";
import { goalGroup } from "/static/views/routine-config-goal.js";
import { identityGroup } from "/static/views/routine-config-identity.js";
import { limitsGroup } from "/static/views/routine-config-limits.js";
import { modelsGroup } from "/static/views/routine-config-models.js";
import { scheduleGroup } from "/static/views/routine-config-schedule.js";

/**
 * renderSettings(view, d, settings, opts) → { refreshHead, refreshSurface, reloadSettings,
 *                                              onRunFinished, dispose }
 * `d` is the routine detail read, `settings` the GET …/settings payload the page fetched with it.
 */
export function renderSettings(view, d, settings, { slug, titleH1, chipHost, runChip, repaintSetup,
                                                    recipeFile }) {
  const form = createSettingsForm(slug, settings);
  // ONE read of /api/library for the whole page — it LINTS the whole library per call (≈4 s on
  // the fleet). The rule picker and the shared-reminders picker both read it.
  const library = api("/api/library").catch(() => ({ rules: [], reminders: [] }));
  const headListeners = new Set();
  const detailListeners = new Set();
  const disposers = [];

  let surfaceRead = 0;                  // two quick re-reads: the older answer must not land last
  let surfacePanel = null;
  const surfaceListeners = new Set();
  async function refreshSurface() {
    const mine = ++surfaceRead;
    let next = null;
    try { next = await api(`/api/routines/${slug}/surface`); }
    catch { /* each reader repaints from its own read, or renders unavailable */ }
    if (mine !== surfaceRead) return;
    d.surface = next;
    surfacePanel?.refresh(next);
    repaintSetup?.(next);
    for (const fn of surfaceListeners) fn(next);
  }

  /** The header chip and the next-fire line, in place. A run starting or finishing moves them;
   *  so does an accepted schedule. */
  async function refreshHead() {
    try {
      const nd = await api(`/api/routines/${slug}`);
      d.next_fire = nd.next_fire;
      chipHost.replaceChildren(runChip(nd));
      for (const fn of headListeners) fn(d);
      return nd;
    } catch { return null; /* cosmetic — the change itself already landed */ }
  }

  /** After an accept: a fresh detail read for the readers that show more than the settings —
   *  a new webhook's URL, the conduct-doc catalogue the ability cards are built from. */
  async function reloadDetail() {
    const nd = await refreshHead();
    if (!nd) return;
    Object.assign(d, nd, { surface: d.surface });
    for (const fn of detailListeners) fn(d);
  }

  /** The saved settings moved under the page (a grant decided elsewhere, a run proving an
   *  outcome): re-read them without dropping the person's unaccepted edits. */
  async function reloadSettings() {
    try { form.rebase(await loadSettings(slug)); } catch { /* the page keeps what it shows */ }
  }

  const ctx = {
    slug, d, form, library, view,
    refreshSurface, refreshHead, reloadDetail, reloadSettings,
    onHead: (fn) => headListeners.add(fn),
    onDetail: (fn) => detailListeners.add(fn),
    onSurface: (fn) => surfaceListeners.add(fn),
    setSurfacePanel: (panel) => { surfacePanel = panel; },
    addDisposer: (fn) => disposers.push(fn),
    jump: (id) => reveal(document.getElementById(id)),
  };

  const goal = goalGroup(ctx);
  view.append(
    el("h2", { id: "sec-settings" }, "Settings"),
    el("p", { class: "set-desc muted small" },
      "Every control below edits a draft. A changed value is marked where it sits and in the "
      + "group that holds it; nothing reaches the routine until you accept the changes at the "
      + "foot of the page. A value that departs from the routine's pattern is marked as an "
      + "override, with the pattern's own value beside it."),
    draftBanner(form),
    patternBar(form),
    el("div", { class: "rgroups", "data-settings-groups": "" },
      scheduleGroup(ctx), goal.node, abilitiesGroup(ctx), accessGroup(ctx), limitsGroup(ctx),
      modelsGroup(ctx), identityGroup(ctx, { titleH1, recipeFile })),
    acceptBar(form, {
      describeSwitch: () => (form.pendingPattern ? "and the pattern switch" : "and following no pattern"),
      onAccepted: async (next) => {
        if (next.fields?.name) titleH1.textContent = next.fields.name;
        await reloadDetail();
        goal.reload();
        refreshSurface();
      },
    }));

  return {
    refreshHead,
    refreshSurface,
    reloadSettings,
    onRunFinished: () => { goal.reload(); reloadSettings(); },
    dispose: () => disposers.forEach((fn) => { try { fn(); } catch { /* gone */ } }),
  };
}
