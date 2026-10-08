// The two pieces both DEVELOPMENT views (views/changes.js, views/changes-routine.js) need to
// show months of rebuilt history without spending the page on it:
//
//   rebuiltMark — "≈": a reading drawn on runs REBUILT from history (migrate_runrecords.py), what
//                 could not be recovered left out. A faint glyph beside the verdict, the words in
//                 its tooltip; with a count, how many of a group's runs were rebuilt.
//   foldEntries — the first `keep` entries of a table and ONE row that reveals the rest. An entry
//                 is the rows that belong together (a change and its signal detail, a group and
//                 its members), so a fold never splits one. Folding is a CLASS, not `hidden`:
//                 an entry's own disclosure state lives in `hidden`, and unfolding must hand
//                 back exactly what the reader had opened. `state` is the view's Set of open
//                 folds, so a live re-read keeps a list the reader expanded expanded.

import { el } from "/static/util.js";

export const REBUILT = "rebuilt from history — what could not be recovered is left out";

export function rebuiltMark(count = 0) {
  return el("span", { class: "rebuilt-mark", "data-rebuilt": count || "",
                      title: count ? `${count} of these runs ${REBUILT}` : REBUILT },
    count ? `≈ ${count}` : "≈");
}

export function foldEntries(entries, { keep, key, state, closed, opened = "show fewer" }) {
  const rows = entries.flat();
  if (entries.length <= keep) return rows;
  const rest = entries.slice(keep).flat();
  const btn = el("button", { type: "button", class: "btn small ghost", "data-fold": key });
  const paint = () => {
    const open = state.has(key);
    for (const r of rest) r.classList.toggle("folded", !open);
    btn.textContent = open ? opened : closed;
    btn.setAttribute("aria-expanded", String(open));
  };
  btn.addEventListener("click", () => {
    if (state.has(key)) state.delete(key); else state.add(key);
    paint();
  });
  paint();
  return [...rows, el("tr", { class: "fold-row" }, el("td", { colspan: 99 }, btn))];
}
