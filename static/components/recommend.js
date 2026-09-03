// "Recommended setup" — the INVERSE of the setup surface. The surface reads FORWARD from what a
// routine holds ("what does this still need?"); this reads from what the routine DOES and answers
// "is this set of rules and permissions right for that?". One system-model pass over the recipe
// and the two catalogs (GET /api/routines/{slug}/recommendations) returns a verdict + a one-line
// reason per catalog item; this panel surfaces only the MISMATCHES — recommended-but-unheld and
// held-but-unneeded — with the aligned ones counted, not listed.
//
// Advisory by design: it never flips a switch. The two panels right below (Permissions &
// capabilities, General rules) are where a toggle changes, so the user stays the one who decides —
// this only tells them what a second reading of the recipe suggests, and why.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

const KIND_LABEL = { permission: "permission", rule: "rule" };

export function recommendPanel(slug) {
  const host = el("div", { class: "recommend-panel" });
  const out = el("div", { class: "recommend-out", style: "margin-top:8px" });
  const btn = el("button", { class: "btn", onclick: run }, "Recommend for this routine");
  host.append(el("div", { class: "row" }, btn), out);

  async function run() {
    btn.disabled = true;
    out.replaceChildren(el("div", { class: "muted small" }, "reading the recipe…"));
    let data;
    try {
      data = await api(`/api/routines/${slug}/recommendations`);
    } catch (e) {
      // A fetch that never received a response — a dropped connection, a proxy idle-timeout, or a
      // deploy in progress — rejects with a bare browser "NetworkError…" and carries no `e.status`
      // (api.js sets that only on an HTTP error response). The recommend pass is one slow
      // system-model read of the whole recipe, so this is the likely failure; say what happened
      // and what to do, rather than leaking the raw string. An HTTP error keeps its legible detail.
      const noResponse = e == null || e.status === undefined;
      out.replaceChildren(el("div", { class: "muted small" },
        noResponse
          ? "the recommender didn't answer in time — it reads the whole recipe with the system "
            + "model, and the connection closed before it finished (a slow model, a proxy timeout, "
            + "or a deploy in progress). Try again in a moment; the Permissions and General rules "
            + "panels below work without it."
          : `couldn't get a recommendation: ${e?.message || e}`));
      btn.disabled = false;
      return;
    }
    btn.disabled = false;
    render(data);
  }

  function recSection(title, rows, cls) {
    if (!rows.length) return null;
    return el("div", { class: `rec-group ${cls}`, style: "margin-top:10px" },
      el("div", { class: "lbl" }, `${title} · ${rows.length}`),
      el("ul", { class: "rec-list", style: "margin:4px 0 0;padding-left:0;list-style:none" },
        ...rows.map((r) =>
          el("li", { class: "rec-row", "data-slug": r.slug, style: "margin:6px 0" },
            el("span", { class: "rec-name", style: "font-weight:600" }, r.slug),
            el("span", { class: "rec-kind muted small" }, ` · ${KIND_LABEL[r.kind] || r.kind}`),
            el("div", { class: "rec-reason muted small" }, r.reason || "")))));
  }

  function render(data) {
    const items = data.items || [];
    if (!data.available) {
      out.replaceChildren(el("div", { class: "muted small" },
        "the recommender is unavailable right now (no model endpoint answered). The Permissions "
        + "and General rules panels below still work."));
      return;
    }
    // A recommendation is only worth a row when it DIFFERS from the current state: a set that
    // already matches the recipe should read as "looks right", not as twenty lines to re-confirm.
    const add = items.filter((i) => i.recommend && !i.held);
    const drop = items.filter((i) => !i.recommend && i.held);
    const aligned = items.length - add.length - drop.length;
    if (!add.length && !drop.length) {
      out.replaceChildren(el("div", { class: "small", "data-rec-aligned": "" },
        `Looks right — all ${items.length} rules & permissions match what this routine does.`));
      return;
    }
    const n = add.length + drop.length;
    out.replaceChildren(
      el("div", { class: "small", style: "margin-bottom:2px" },
        el("b", {}, String(n)), ` suggested change${n === 1 ? "" : "s"} · ${aligned} already aligned.`),
      el("div", { class: "muted small" },
        "Nothing here is applied — change any of these in the Permissions and General rules panels below."),
      recSection("Consider adding", add, "rec-add"),
      recSection("Consider removing", drop, "rec-drop"));
  }

  return host;
}
