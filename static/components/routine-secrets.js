// The routine's OWN secrets (D103) — the private half of the two-scope store.
//
// `secrets.d/<slug>.env` lives beside config.yaml, NEVER in the routine dir, which is
// `git add -A` autocommitted and auto-pushed. Ownership is the grant: a scoped secret needs
// no exposure row and asks nobody, and it overrides a central value of the same name for
// this routine's runs. Values are write-only here, exactly as in Settings → Secrets — the
// API answers with names.
//
// Its own card (rather than more lines in routine-config.js) for the same reason
// connections and machines have theirs: one panel, one responsibility, one place to fix.

import { api } from "/static/api.js";
import { el, skeleton, toast } from "/static/util.js";

export function routineSecretsCard(slug) {
  const box = el("div", {}, skeleton(["50%"]));

  const load = async () => {
    let own;
    try { own = await api(`/api/routines/${slug}/secrets`); }
    catch (err) { box.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    box.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:8px" },
      "Credentials that belong to THIS routine alone. They need no exposure decision — ",
      "ownership is the grant — and a util still receives one only if it declares the name ",
      "on its ", el("code", {}, "secrets:"), " header. A name set here overrides the central ",
      "store's value for this routine's runs."));

    for (const name of own.keys || []) {
      box.append(el("div", { class: "row", style: "margin:5px 0", "data-own-secret": name },
        el("code", { class: "small", style: "min-width:240px" }, name),
        (own.shadowing || []).includes(name)
          ? el("span", { class: "muted small" }, "overrides the central store's value")
          : null,
        el("button", { class: "btn small", onclick: async () => {
          try {
            await api(`/api/routines/${slug}/secrets/${encodeURIComponent(name)}`,
                      { method: "DELETE" });
            toast(`${name} removed`); load();
          } catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "remove")));
    }
    if (!(own.keys || []).length)
      box.append(el("div", { class: "muted small" }, "none yet"));

    const keyIn = el("input", { type: "text", placeholder: "NAME",
                                "data-own-secret-key": "", style: "min-width:240px" });
    const valIn = el("input", { type: "password", placeholder: "value",
                                "data-own-secret-value": "", style: "min-width:240px" });
    box.append(el("div", { class: "row", style: "margin-top:10px" }, keyIn, valIn,
      el("button", { class: "btn small", "data-own-secret-set": "", onclick: async () => {
        if (!keyIn.value.trim()) { toast("a name is required", 3000, { error: true }); return; }
        try {
          await api(`/api/routines/${slug}/secrets`,
                    { method: "PUT", body: { key: keyIn.value.trim(), value: valIn.value } });
          toast(`${keyIn.value.trim()} saved`); keyIn.value = ""; valIn.value = "";
          load();
        } catch (err) { toast(err.message, 4000, { error: true }); }
      } }, "set")));
  };

  load();
  return box;
}
