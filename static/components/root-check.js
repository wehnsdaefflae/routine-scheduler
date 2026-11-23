// The ROOT COMMAND CHECK control (rsched/rootmodes.py): which calls of a run holding a root
// login the decision model rates before they run — the `remote` util's commands to such a
// machine (the default), every tool call, or none. Shared by the routine page (a draft field,
// `onChange`) and the conversation header (`onSave`). Each warning a run raises offers the
// same three, for that run or from now on.

import { el, toast, toastError } from "/static/util.js";

export const ROOT_CHECK_LABELS = {
  remote: "remote commands only", any: "every tool call", off: "off",
};

export function rootCheckControl(value, { onChange, onSave } = {}) {
  const select = el("select", { "data-root-check": "" },
    Object.entries(ROOT_CHECK_LABELS).map(([mode, label]) =>
      el("option", { value: mode, selected: mode === (value || "remote") ? "" : null }, label)));
  if (onChange) select.addEventListener("change", () => onChange(select.value));
  if (onSave) select.addEventListener("change", async () => {
    try { await onSave(select.value); toast(`root command check: ${ROOT_CHECK_LABELS[select.value]}`); }
    catch (err) { toastError(err); }
  });
  return el("div", {},
    el("label", { class: "row", style: "gap:8px" },
      el("span", { class: "small" }, "check before running"), select),
    el("div", { class: "muted small", style: "margin-top:4px" },
      "Applies while this holds a machine whose login has root rights (marked ",
      el("strong", {}, "root"), " in the machine list). Each checked call goes to the decision ",
      "model with the five calls before it; a risky one waits for your approval, which can also ",
      "change this setting for that run or from now on."));
}
