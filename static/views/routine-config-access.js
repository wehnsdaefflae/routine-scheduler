// Routine settings — SECRETS & ACCESS (D103): which of the SHARED store's secrets this routine's
// util calls may receive, the access requests it declined FOREVER, and its OWN private store.
//
// The first two are one setting — routine.yaml's `grants:` (entity ids, entities.py):
// `secret:<NAME>` rows are the exposure map; a FALSE row of any other class is a deny-forever
// tombstone an access request left behind. Both editors therefore edit the one draft value and
// each rebuilds when the other moves it. A grant is never applied without a person's click —
// a proposal may carry one; it waits for the accept like everything else.
//
// The routine's own secrets are not a setting: a value set there is stored at once and never
// shown again (the API answers with names).

import { el, skeleton } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { fieldBlock, settingsGroup } from "/static/components/settings-field.js";
import { ownSecretsCard } from "/static/components/routine-secrets.js";
import { declinedIds, declinedRows } from "/static/components/declined-access.js";
import { exposureDials, exposureRows, secretStore } from
  "/static/components/secret-exposure.js";

const SECRET = "secret:";

export function accessGroup(ctx) {
  const { slug, form } = ctx;
  let store = secretStore();

  // The dials come from components/secret-exposure.js, which a CONVERSATION's capabilities
  // panel also mounts (D216 part a — before it, this block was the only three-state exposure
  // control in the instance and a conversation had none). Only the WRITE differs: here the
  // change moves the settings DRAFT the operator then accepts, which is why a wholesale
  // `grants` replace is safe on this page and not on that one.
  const exposure = fieldBlock(form, "grants", (value, set) => {
    const host = el("div", {}, skeleton(["50%"]));
    store.then((sec) => {
      const grants = value || {};
      const rows = exposureRows(grants);
      const names = [...new Set([...(sec.keys || []), ...Object.keys(rows)])].sort();
      host.replaceChildren(...[
        sec.error ? el("div", { class: "muted small" }, sec.error) : null,
        exposureDials(names, rows, (name, state) => {
          const next = { ...grants };
          if (state === null) delete next[`${SECRET}${name}`];
          else next[`${SECRET}${name}`] = state;
          set(next);
        }, { keys: sec.keys || [] }),
      ].filter(Boolean));
    });
    return host;
  }, { noWas: true });

  // The rows themselves come from components/declined-access.js, which a CONVERSATION's
  // capabilities panel also mounts (F783 — before it, this block was the only editor in the
  // instance that could clear a deny-forever tombstone, and conversations had no equivalent).
  // Only the WRITE differs: here the press moves the settings DRAFT the operator then accepts,
  // which is why a wholesale `grants` replace is safe on this page and not on that one.
  const declined = fieldBlock(form, "grants", (value, set) => {
    const grants = value || {};
    return declinedRows(declinedIds(grants), (eid) => {
      const next = { ...grants };
      delete next[eid];
      set(next);
    });
  }, { noWas: true });

  // F193: a grant decided elsewhere (a Decisions-page approval) lands in routine.yaml while this
  // page is open. The web layer persists a forever-decision BEFORE publishing the answer event,
  // so one re-read suffices — and it goes through the draft, so an edit you have not accepted
  // survives it.
  const onBus = (e) => {
    if (e.detail?.event === "question_answered" && e.detail.routine === slug) {
      store = secretStore();
      ctx.reloadSettings();
      ctx.refreshSurface();
    }
  };
  window.addEventListener("rsched-bus", onBus);
  ctx.addDisposer(() => window.removeEventListener("rsched-bus", onBus));

  return settingsGroup({
    form, title: "Secrets & access", hint: "shared-store exposure · its own credentials · settled denials",
    keys: ["grants"],
    digest: () => {
      const n = declinedIds(form.get("grants")).length;
      return n ? `${n} declined forever` : "nothing declined";
    },
    sections: [
      ...settingsSection({ title: "Secret exposure", id: "secret-exposure" },
        ["which of the SHARED store's secrets this routine's util calls may receive. An undecided ",
         "secret is asked about the FIRST time a util call declares it — a blocking access request, ",
         "whose answer is remembered here. Manage the secrets themselves in ",
         el("a", { href: "#/settings?section=secrets" }, "Settings → Secrets"), "."],
        exposure.node),
      ...settingsSection({ title: "Own secrets", id: "own-secrets" },
        ["the private half of the two-scope store: credentials belonging to THIS routine, as ",
         "opposed to the shared-store names it is exposed to above. Setting or removing one takes ",
         "effect at once — it is a credential, not a setting."],
        ownSecretsCard(slug)),
    ],
    more: [
      ...settingsSection({ title: "Declined access", id: "declined-access" },
        ["access this routine's requests were declined FOREVER — it no longer asks for these; the ",
         "engine refuses them. Removing a row returns the entity to undecided (requestable again)."],
        declined.node),
    ],
  });
}
