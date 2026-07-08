// New-routine wizard — full version arrives with M4 (clarify-instruction chat →
// workflow suggestion → schedule → scaffold).

import { el } from "/static/util.js";

export async function render(view) {
  view.append(el("h1", {}, "New routine"),
    el("div", { class: "empty" },
      "The instruction wizard ships with milestone M4. Until then, scaffold by hand: ",
      "copy an existing routine dir under ~/routines/ and edit routine.yaml."));
}
