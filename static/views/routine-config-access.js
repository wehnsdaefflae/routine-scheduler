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

import { api } from "/static/api.js";
import { el, skeleton } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { fieldBlock, settingsGroup } from "/static/components/settings-field.js";
import { routineSecretsCard } from "/static/components/routine-secrets.js";

const SECRET = "secret:";

export function accessGroup(ctx) {
  const { slug, form } = ctx;
  let store = api("/api/settings/secrets").catch((err) => ({ error: err.message, keys: [] }));

  const exposure = fieldBlock(form, "grants", (value, set) => {
    const host = el("div", {}, skeleton(["50%"]));
    store.then((sec) => {
      const grants = value || {};
      const rows = Object.fromEntries(Object.entries(grants)
        .filter(([k]) => k.startsWith(SECRET)).map(([k, v]) => [k.slice(SECRET.length), v]));
      const names = [...new Set([...(sec.keys || []), ...Object.keys(rows)])].sort();
      host.replaceChildren(...[
        sec.error ? el("div", { class: "muted small" }, sec.error) : null,
        names.length ? null : el("div", { class: "muted small" }, "no secrets in the store yet"),
        ...names.map((name) => {
          const sel = el("select", { "data-nopersist": true,
            onchange: () => {
              const next = { ...grants };
              if (sel.value) next[`${SECRET}${name}`] = sel.value === "true";
              else delete next[`${SECRET}${name}`];
              set(next);
            } },
            el("option", { value: "" }, "ask on first use"),
            el("option", { value: "true" }, "expose"),
            el("option", { value: "false" }, "withhold"));
          sel.value = name in rows ? String(!!rows[name]) : "";
          return el("div", { class: "row", style: "margin:5px 0", "data-secret-row": name },
            el("code", { class: "small", style: "min-width:240px" }, name), sel,
            (sec.keys || []).includes(name) ? null
              : el("span", { class: "muted small" }, "not in the store (stale entry)"));
        }),
      ].filter(Boolean));
    });
    return host;
  }, { noWas: true });

  const declined = fieldBlock(form, "grants", (value, set) => {
    const grants = value || {};
    const rows = Object.keys(grants).filter((k) => !k.startsWith(SECRET) && grants[k] === false)
      .sort();
    if (!rows.length) return el("div", { class: "muted small" }, "nothing declined");
    return el("div", {}, ...rows.map((eid) =>
      el("div", { class: "row", style: "margin:5px 0", "data-declined-row": eid },
        el("code", { class: "small", style: "min-width:240px" }, eid),
        el("button", { type: "button", class: "btn small", title: "make it requestable again",
          onclick: () => {
            const next = { ...grants };
            delete next[eid];
            set(next);
          } }, "remove"))));
  }, { noWas: true });

  // F193: a grant decided elsewhere (a Decisions-page approval) lands in routine.yaml while this
  // page is open. The web layer persists a forever-decision BEFORE publishing the answer event,
  // so one re-read suffices — and it goes through the draft, so an edit you have not accepted
  // survives it.
  const onBus = (e) => {
    if (e.detail?.event === "question_answered" && e.detail.routine === slug) {
      store = api("/api/settings/secrets").catch((err) => ({ error: err.message, keys: [] }));
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
      const g = form.get("grants") || {};
      const n = Object.keys(g).filter((k) => !k.startsWith(SECRET) && g[k] === false).length;
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
        routineSecretsCard(slug)),
    ],
    more: [
      ...settingsSection({ title: "Declined access", id: "declined-access" },
        ["access this routine's requests were declined FOREVER — it no longer asks for these; the ",
         "engine refuses them. Removing a row returns the entity to undecided (requestable again)."],
        declined.node),
    ],
  });
}
