// The run page's MID-RUN model switch: which catalog model the MAIN role runs on and how hard it
// reasons, applied at the run's next turn boundary (the engine re-resolves both every turn).
// Model and effort are independent settings — a blank effort leaves the level as it is, so the
// same control raises thinking without touching the model. The durable values stay on the
// routine page; this is run-scoped, like the deliberation re-level beside it.

import { api } from "/static/api.js";
import { el, toast, toastError } from "/static/util.js";

/** createModelSwitch(runId, modelSpan) → { node, setModel }
 *  `modelSpan` is the run bar's "model <name>" label; `setModel(m)` keeps it and the select in
 *  step with the model status.json reports. */
export function createModelSwitch(runId, modelSpan) {
  const node = el("details", { class: "small" },
    el("summary", { style: "cursor:pointer;color:var(--ink-2)" }, "⚙ switch model / effort"));
  let mSelRef = null, curModel = "";   // the switch-select mirrors the LIVE model (F166)
  // F191: status.json reports the live model as an "endpoint/model" id while the switch
  // select lists catalog NAMES — resolve either form to the catalog name, otherwise the
  // assignment silently no-ops and the select shows option #1 as if it were the setting.
  let resolveModel = (m) => m;   // replaced once the catalog arrives
  const syncSel = () => {
    if (!mSelRef || !curModel) return;
    const name = resolveModel(curModel);
    if ([...mSelRef.options].some((o) => o.value === name)) mSelRef.value = name;
  };
  const setModel = (m) => {
    if (m) curModel = m;
    modelSpan.textContent = m ? `model ${m}` : "";
    syncSel();
  };
  api("/api/settings/models").then((d) => {
    const models = d.models || [];
    if (!models.length) return;
    resolveModel = (m) => (models.find((x) => x.name === m
      || (x.endpoint && x.model && `${x.endpoint}/${x.model}` === m)) || { name: m }).name;
    const mSel = el("select", { class: "tight", style: "width:auto;padding:3px 6px",
      "aria-label": "main model" }, models.map((m) => el("option", { value: m.name }, m.name)));
    const eSel = el("select", { class: "tight", style: "width:auto;padding:3px 6px",
      "aria-label": "main reasoning effort", "data-effort-role": "main" },
      el("option", { value: "" }, "effort: keep"),
      (d.effort_levels || []).map((l) => el("option", { value: l }, l)));
    mSelRef = mSel;
    syncSel();   // preselect the run's actual model (name OR endpoint/model id), not option #1
    const go = el("button", { class: "btn small primary" }, "switch");
    go.onclick = async () => {
      try {
        const r = await api(`/api/runs/${runId}/model`, { method: "POST",
          body: { model: mSel.value, effort: eSel.value } });
        toast(`${r.switch} — takes effect next turn`);
      } catch (err) { toastError(err); }
    };
    node.append(el("div", { class: "row mt", style: "gap:5px" }, mSel, eSel, go));
  }).catch(() => {});
  return { node, setModel };
}
