// Recipe health (split from routine.js): runs bucketed by the recipe version that
// produced them (engine-stamped commit; the durable usage stream survives retention),
// a deterministic regression flag on the newest change, and the one-click roll-back.
// mountHealth fills `box` and returns { reload } for the run-lifecycle bus handler.
//
// Plus the CAUTIONS the between-turn feed raises here — the reminders in force with this
// routine's own tally, and the rule assists that have fired. Same tab because they answer
// the same question the version table does: is this routine's behaviour getting better or
// worse, and what changed. A caution that fires constantly and is labelled could_not every
// time is a bad pattern, and the tally is kept precisely so that is reviewable — it just had
// nowhere to be read.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, fmtNum, queuedToast, toast } from "/static/util.js";

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
        `Roll back recipe change ${label}? main.md / stages / tuning.yaml return to their state just before it (a new commit — nothing is lost). Config and state are untouched.`,
        { confirmLabel: "roll back" }))) return;
      try {
        const res = await api(`/api/routines/${slug}/recipe/revert`,
          { method: "POST", body: { commit } });
        queuedToast(res, "recipe rolled back"); onRecipeChanged(); reload();
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
    parts.push(...cautionSections(h.cautions || {}));
    box.replaceChildren(...parts);
  }
  async function forget(rid, regex) {
    if (!(await confirmDialog(
      `Delete this routine's reminder ${rid} (${regex})? Its tally goes with it — the ` +
      "definition is what the counts are about. Runs stop being held on it immediately.",
      { confirmLabel: "delete" }))) return;
    try {
      await api(`/api/routines/${slug}/reminders/${rid}`, { method: "DELETE" });
      toast("reminder deleted"); reload();
    } catch (err) { toast(err.message, 5000, { error: true }); }
  }

  // The four labels in the order they read as a verdict: the first says the pattern is too
  // broad, the last two say it fired on a real call. A row where could_not dominates is the
  // one to delete, so it is the first number the eye lands on.
  function cautionSections(c) {
    const reminders = c.reminders || [], assists = c.assists || [];
    if (!reminders.length && !assists.length) return [];
    const labels = c.labels || [];
    const out = [el("h3", { class: "mt" }, "cautions"),
      el("div", { class: "muted small" },
        "what the between-turn feed raised here — a hold costs the run a turn, so a pattern " +
        "that fires without ever changing a decision is one to delete")];
    if (reminders.length) {
      const rows = reminders.map((r) => {
        const st = r.stats || {};
        return el("tr", {},
          el("td", {}, el("span", { class: "ref-tag", title: r.id }, r.id),
            el("span", { class: `chip ${r.scope === "global" ? "" : "ok"}`,
                         style: "margin-left:6px" }, r.scope)),
          el("td", { class: "mono small", title: r.regex }, r.regex),
          el("td", { class: "muted", title: r.description }, r.description),
          el("td", { class: "num" }, String(st.fires || 0)),
          ...labels.map((k) => el("td", { class: "num" }, st[k] ? String(st[k]) : "—")),
          el("td", {}, r.scope === "local"
            ? el("button", { class: "btn small danger", onclick: () => forget(r.id, r.regex) },
                "delete")
            // A curated reminder belongs to the library; this routine owns only its tally.
            : el("span", { class: "muted small", title: "curated — remove it on the Library tab" },
                "library")));
      });
      out.push(el("div", { class: "tablewrap" }, el("table", { class: "list" },
        el("thead", {}, el("tr", {}, ["reminder", "pattern", "consequence", "fires", ...labels, ""]
          .map((x) => el("th", {}, x)))),
        el("tbody", {}, ...rows))));
    }
    if (assists.length) {
      out.push(el("div", { class: "muted small mt" }, "rule assists that have fired"),
        el("div", { class: "tablewrap" }, el("table", { class: "list" },
          el("thead", {}, el("tr", {}, ["rule", "moment", "payload", "fires"]
            .map((x) => el("th", {}, x)))),
          el("tbody", {}, ...assists.map((a) => el("tr", {},
            el("td", { title: a.key }, a.rule,
              a.retired ? el("span", { class: "muted small" },
                " · no longer bound") : ""),
            el("td", { class: "muted" }, a.moment || "—"),
            el("td", { class: "muted" }, a.payload || "—"),
            el("td", { class: "num" }, String(a.fires))))))));
    }
    return out;
  }

  reload();
  return { reload };
}
