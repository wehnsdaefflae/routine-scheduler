// Settings -> the scheduler's own source repository - split from settings.js (one section per module; settings.js keeps
// the section order, nav, and deep-link jump). Appends its own panel and returns the fill
// promise so settings.js can await all sections before the anchor jump.

import { api } from "/static/api.js";
import { el, toast } from "/static/util.js";
import { panelSection, remoteTester } from "/static/views/settings-common.js";

export function renderSource(view) {
  // -- scheduler source repository (self-audit's push target) ---------------------
  // Only the GET is guarded, which is what panelSection guards for every other section: a
  // throw while BUILDING the controls below would formerly have been painted into the panel
  // as a bare message, and that is a console bug rather than an unreachable-server report.
  return panelSection(view, "/api/settings/source", ["60%", "90%"], (srcBox, src) => {
    srcBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
      "The scheduler's own code repo — where the self-audit routine commits and pushes its changes. ",
      "Set the remote to the fork those autonomous pushes should target."));
    const input = el("input", { type: "text", value: src.remote || "",
      placeholder: "https://github.com/<you>/routine-scheduler.git — empty = local only" });
    const save = el("button", { class: "btn small primary" }, "save + push");
    save.onclick = async () => {
      try {
        const r = await api("/api/settings/source", { method: "PUT", body: { remote: input.value.trim() } });
        toast(r.pushed ? "source: saved + pushed"
          : r.push_error ? `source: saved (push failed: ${r.push_error})` : "source: saved");
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    const t = remoteTester(input);
    srcBox.append(el("div", { class: "row", style: "margin:9px 0" },
      el("span", { class: "ref-tag", style: "min-width:90px;text-align:center" }, src.branch),
      input, t.btn, save));
    srcBox.append(el("div", { style: "margin:-4px 0 8px 98px" }, t.result));
    srcBox.append(el("div", { class: "faint small" },
      src.home + (src.exists ? "" : "  ⚠ not a git repo")));
  });
}
