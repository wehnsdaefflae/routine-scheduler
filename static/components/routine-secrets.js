// A subject's OWN secrets (D103) — the private half of the two-scope store.
//
// `secrets.d/<slug>.env` lives beside config.yaml, NEVER in the subject's own directory, which
// is `git add -A` autocommitted and auto-pushed. Ownership is the grant: a scoped secret needs
// no exposure row and asks nobody, and it overrides a central value of the same name for
// this subject's runs. Values are write-only here, exactly as in Settings → Secrets — the
// API answers with names.
//
// Its own card (rather than more lines in routine-config.js) for the same reason
// connections and machines have theirs: one panel, one responsibility, one place to fix.
//
// ONE card, two subjects (D216 rank 1): the scoped store has always been keyed by the RUN's
// slug, and a conversation's slug is its directory name exactly as a routine's is — so the
// engine already injected `secrets.d/<conversation-slug>.env` into a conversation's util
// calls. Only the write surface was routine-only, and that is a base path, not a mechanism:
// `base` chooses which router serves this card and `subject` the word the copy uses.

import { api } from "/static/api.js";
import { act, el, skeleton, toast } from "/static/util.js";

export function ownSecretsCard(slug, { base = "/api/routines", subject = "routine" } = {}) {
  const root = `${base}/${slug}/secrets`;
  const box = el("div", {}, skeleton(["50%"]));

  const load = async () => {
    let own;
    try { own = await api(root); }
    catch (err) { box.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    box.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:8px" },
      `Credentials that belong to THIS ${subject} alone. They need no exposure decision — `,
      "ownership is the grant — and a util still receives one only if it declares the name ",
      "on its ", el("code", {}, "secrets:"), " header. A name set here overrides the central ",
      `store's value for this ${subject}'s runs.`));

    // Both buttons run through act(), as Settings → Secrets does: disabled while the request is
    // out, so a double press is one write — not a second DELETE answering 404.
    for (const name of own.keys || []) {
      const remove = el("button", { class: "btn small" }, "remove");
      remove.onclick = () => act(remove, async () => {
        await api(`${root}/${encodeURIComponent(name)}`, { method: "DELETE" });
        load();
      }, `${name} removed`);
      box.append(el("div", { class: "row", style: "margin:5px 0", "data-own-secret": name },
        el("code", { class: "small", style: "min-width:240px" }, name),
        (own.shadowing || []).includes(name)
          ? el("span", { class: "muted small" }, "overrides the central store's value")
          : null,
        remove));
    }
    if (!(own.keys || []).length)
      box.append(el("div", { class: "muted small" }, "none yet"));

    const keyIn = el("input", { type: "text", placeholder: "NAME",
                                "data-own-secret-key": "", style: "min-width:240px" });
    const valIn = el("input", { type: "password", placeholder: "value",
                                "data-own-secret-value": "", style: "min-width:240px" });
    const setBtn = el("button", { class: "btn small", "data-own-secret-set": "" }, "set");
    setBtn.onclick = () => act(setBtn, async () => {
      const key = keyIn.value.trim();
      if (!key) { toast("a name is required", 3000, { error: true }); return; }
      await api(root, { method: "PUT", body: { key, value: valIn.value } });
      toast(`${key} saved`); keyIn.value = ""; valIn.value = "";
      load();
    });
    box.append(el("div", { class: "row", style: "margin-top:10px" }, keyIn, valIn, setBtn));
  };

  load();
  return box;
}
