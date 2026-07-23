// Recipe health (split from routine.js): runs bucketed by the recipe version that
// produced them (engine-stamped commit; the durable usage stream survives retention),
// a deterministic regression flag on the newest change, and the one-click roll-back.
// mountHealth fills `box` and returns { reload } for the run-lifecycle bus handler.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, fmtNum, toast } from "/static/util.js";

export function mountHealth(box, slug, { onRecipeChanged }) {
  async function reload() {
    let h;
    try { h = await api(`/api/routines/${slug}/health`); }
    catch (err) { box.replaceChildren(el("div", { class: "muted small" }, `health unavailable: ${err.message}`)); return; }
    const parts = [];
    const day = (iso) => (iso ? String(iso).slice(0, 10) : "—");
    const reg = h.regression || {};
    async function revert(commit, label) {
      if (!(await confirmDialog(
        `Roll back recipe change ${label}? main.md / stages / traits / tuning.yaml return to their state just before it (a new commit — nothing is lost). Config and state are untouched.`,
        { confirmLabel: "roll back" }))) return;
      try {
        await api(`/api/routines/${slug}/recipe/revert`, { method: "POST", body: { commit } });
        toast("recipe rolled back"); onRecipeChanged(); reload();
      } catch (err) { toast(err.message, 5000, { error: true }); }
    }
    if (reg.flagged) {
      parts.push(el("div", { class: "panel err", style: "margin-bottom:10px" },
        el("div", {}, `⚠ possible regression since recipe change ${reg.short} — "${reg.subject}"`),
        ...(reg.reasons || []).map((r) => el("div", { class: "small", style: "margin-top:4px" }, `· ${r}`)),
        el("div", { class: "row mt" },
          el("button", { class: "btn small danger", onclick: () => revert(reg.commit, reg.short) },
            "↩ roll back this change"))));
    }
    if (!h.tracked) {
      parts.push(el("div", { class: "muted small" },
        "no git history in this dir — recipe versions aren't tracked (conversations are unversioned by design)"));
    }
    const versions = h.versions || [];
    if (versions.length) {
      const outcomes = (b) => ["ok", "partial", "failed", "aborted"]
        .filter((k) => b[k]).map((k) => `${b[k]} ${k}`).join(" · ") || "—";
      const rows = versions.map((b, i) => el("tr", {},
        el("td", { title: b.commit || "" },
          el("span", { class: "ref-tag" }, b.short || "?"),
          b.current ? el("span", { class: "chip ok", style: "margin-left:6px" }, "current") : ""),
        el("td", { class: "muted", style: "max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", title: b.subject }, b.subject || ""),
        el("td", { class: "muted" }, day(b.date)),
        el("td", { class: "num" }, String(b.runs)),
        el("td", {}, outcomes(b),
          b.inferred_runs ? el("span", { class: "muted small", title: "runs from before version stamping — attributed by date (can be off by one run around a change)" }, ` · ~${b.inferred_runs} date-mapped`) : ""),
        el("td", { class: "num" }, b.runs ? fmtNum(b.turns_median) : "—"),
        el("td", { class: "num" }, b.runs ? fmtNum(b.tokens_median) : "—"),
        el("td", { class: "num", title: "decisions deferred to you during these runs" }, b.asks_deferred ? String(b.asks_deferred) : "—"),
        el("td", {}, b.current && versions.length > 1 && b.commit
          ? el("button", { class: "btn small", title: "restore the recipe files to their state just before this change",
              onclick: () => revert(b.commit, b.short) }, "↩ roll back")
          : "")));
      parts.push(el("div", { class: "tablewrap" },
        el("table", { class: "list" },
          el("thead", {}, el("tr", {}, ["version", "change", "date", "runs", "outcomes", "med. turns", "med. tokens", "asks", ""].map((x) => el("th", {}, x)))),
          el("tbody", {}, ...rows))));
      if (versions.every((b) => !b.runs)) {
        parts.push(el("div", { class: "muted small mt" }, "no runs recorded in the usage stream yet — health fills in as runs finish"));
      }
    } else if (h.tracked) {
      parts.push(el("div", { class: "muted small" }, "no recipe-touching commits yet — versions appear once the recipe is committed"));
    }
    if (h.untracked?.runs) {
      parts.push(el("div", { class: "muted small mt" },
        `${h.untracked.runs} run(s) could not be attributed to any recipe version`));
    }
    box.replaceChildren(...parts);
  }
  reload();
  return { reload };
}
