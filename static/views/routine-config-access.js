// Routine config — SECRETS & ACCESS (D103): the routine's OWN private credential store, the
// SHARED store's exposure map, and the access requests it was declined FOREVER.
//
// Split out of routine-config.js along routine.js's SECTION_GROUPS; this module is the
// "Secrets & access" group. The last two panels are one object in the file — routine.yaml's
// `grants:` (entity ids, entities.py): `secret:<NAME>` rows are the exposure map, and a FALSE
// row of any other class is a deny-forever tombstone an access request left behind. Saving
// REPLACES the whole mapping, so the two editors always write their rows together, which is
// why they cannot live in two modules.

import { api } from "/static/api.js";
import { el, skeleton, toast, toastError } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { routineSecretsCard } from "/static/components/routine-secrets.js";

/** Returns `dispose` — this group holds a bus listener, and a view teardown must detach it. */
export function accessSections(view, d, { slug, refreshSurface }) {
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
  // `grants` is re-read only when something may have MOVED it (a save here, or a
  // forever-decision answered elsewhere). The first paint uses the detail payload this page
  // was rendered from — re-fetching the whole routine detail at mount just to read one field
  // of it cost a second 2 s request on every routine-page open.
  const loadSecrets = async ({ grants = d.grants || {} } = {}) => {
    let sec;
    try {
      sec = await api("/api/settings/secrets");
    } catch (err) { secBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    const secretRows = Object.fromEntries(Object.entries(grants)
      .filter(([k]) => k.startsWith("secret:")).map(([k, v]) => [k.slice("secret:".length), v]));
    const otherRows = Object.fromEntries(Object.entries(grants)
      .filter(([k]) => !k.startsWith("secret:")));
    const saveGrants = async (updated, note) => {
      try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { grants: updated } });
        // an exposure decision settles a `secret:` row; clearing a tombstone reopens one.
        // The server re-applies its own rules on save, so the repaint reads what it STORED.
        toast(note); reloadSecrets(); refreshSurface(); }
      catch (err) { toastError(err); }
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
  /** Re-read the routine's CURRENT grants and repaint both editors — for the two moments a
   *  grant can move under an open page: a save here, and a forever-decision answered
   *  elsewhere. F193 heritage: never render the page-load snapshot forever. */
  const reloadSecrets = async () => {
    try { await loadSecrets({ grants: (await api(`/api/routines/${slug}`)).grants || {} }); }
    catch (err) { secBox.replaceChildren(el("div", { class: "muted" }, err.message)); }
  };
  loadSecrets();
  // The web layer persists a forever-decision BEFORE publishing the answer event, so one
  // refetch suffices.
  const onSecretsBus = (e) => {
    if (e.detail?.event === "question_answered" && e.detail.routine === slug) {
      reloadSecrets();
      refreshSurface();   // a forever-decision is a grant, so it settles a row up here too
    }
  };
  window.addEventListener("rsched-bus", onSecretsBus);

  return { dispose: () => window.removeEventListener("rsched-bus", onSecretsBus) };
}
