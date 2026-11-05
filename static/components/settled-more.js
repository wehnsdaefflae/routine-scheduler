// The Settled band's honest edge (F810). /api/questions sends the SETTLED archive only as a
// window the Decisions page asks for (web/decisions_window.py), so this page shows the newest
// few and SAYS so — "the newest 25 of 389" — with a button for more, instead of a list that
// silently stops. The full history stays in each routine's questions/answered/ either way.

import { act, el } from "/static/util.js";
import { wantSettled, loadQuestions } from "/static/questions-store.js";

/** Settled rows per window step — the first window and each "show more". */
export const SETTLED_PAGE = 25;

/** The footer under the list, or null when every settled decision is already shown.
 *  `snapshot` is the store's (its `settled` window and `settledTotal`); `onLoaded` repaints
 *  from the wider snapshot the button fetched. */
export function settledMore(snapshot, onLoaded) {
  const total = snapshot?.settledTotal || 0;
  const shown = Math.min(snapshot?.settled || 0, total);
  if (shown >= total) return null;
  const more = el("button", { class: "btn small", "data-settled-more": "" },
    `show ${Math.min(SETTLED_PAGE, total - shown)} older`);
  more.onclick = () => act(more, async () => {
    wantSettled(shown + SETTLED_PAGE);
    onLoaded(await loadQuestions());
  });
  return el("div", { class: "row mt faint small", style: "gap:10px" },
    el("span", {}, `settled: showing the newest ${shown} of ${total}`), more);
}
