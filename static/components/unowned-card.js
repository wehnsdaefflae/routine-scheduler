// The Decisions page's UNOWNED-WORK card — a report that has waited over a week with no routine
// that would act on it: addressed to nobody, or to a routine that starts no run
// (daemon/owed_watch.py). R2044, "nobody owns deploying the Steward kit", sat three weeks this
// way. Two decisions, both executed by the web as a report FROM the operator
// (web/api_pending._decide_unowned): give it to a routine — the routine that filed it and the
// usual owners are offered first, any other slug may be typed — or close it.

import { api } from "/static/api.js";
import { el, toast, toastError, when } from "/static/util.js";

export const UNOWNED = "unowned-report";

export function unownedRow(rec, { reload, onChanged } = {}) {
  const f = rec.fields || {};
  const pick = el("select", { class: "small", "aria-label": "routine to give it to" },
    ...(f.suggest || []).map((s) => el("option", { value: s }, s)),
    el("option", { value: "" }, "another routine…"));
  const other = el("input", { class: "small mono", placeholder: "routine slug", hidden: true });
  pick.onchange = () => { other.hidden = pick.value !== ""; };
  other.hidden = pick.value !== "";
  const route = el("button", { class: "btn small primary" }, "give it to");
  const close = el("button", { class: "btn small" }, "close it");
  const act = async (body, said) => {
    route.disabled = close.disabled = true;
    try {
      await api(`/api/pending-creations/${rec.id}/materialize`, { method: "POST", body });
      toast(said, 5000); await reload?.(); onChanged?.();
    } catch (err) { toastError(err, 5000); route.disabled = close.disabled = false; }
  };
  route.onclick = () => {
    const to = pick.value || other.value.trim();
    if (to) act({ choice: "route", to }, `${f.report} is ${to}'s now`);
  };
  close.onclick = () => act({ choice: "close" }, `${f.report} closed`);
  return el("div", { class: "card mt", "data-unowned": f.report || "" },
    el("div", { class: "row", style: "gap:10px;align-items:center;flex-wrap:wrap" },
      el("span", {}, el("strong", {}, f.report || "?"), ` · no owner for ${f.days} days`),
      el("span", { style: "margin-left:auto" }), route, pick, other, close),
    el("div", { class: "small mt" }, f.title || ""),
    el("div", { class: "faint small" },
      `filed by ${f.from || "?"}`, f.to ? ` · addressed to ${f.to}, which starts no run` : " · addressed to nobody",
      " · ", when(rec.created_at), " · ", el("a", { href: "#/items" }, "the items")));
}
