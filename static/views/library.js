// Workflow library browser — full version arrives with M4 (lint badges, editor, proposals).

import { el } from "/static/util.js";

export async function render(view) {
  view.append(el("h1", {}, "Workflow library"),
    el("div", { class: "empty" },
      "The workflow library UI ships with milestone M4 (library browser, lint status, ",
      "git history, editor, meta-routine proposals)."));
}
