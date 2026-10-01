// Landing on a node: the two moves every journey makes that sends the reader to something on
// the page — an item card (reflinks.js focusRef), a settings field (settings-field.js reveal), a
// setup fix's section (surface-view.js), a page section (toc.js). Each of the four used to write
// them out by hand.

/** Open every <details> on the way to `node`: inside a closed fold it has no box, so a scroll
 *  to it lands nowhere. */
export function openFolds(node) {
  for (let d = node.closest("details"); d; d = d.parentElement?.closest("details")) d.open = true;
}

/** Light `node` with the landing outline (views.css `.ref-flash`, a 2.4 s animation), and take
 *  the class off once it has played so the next landing on the same node animates again. */
export function flash(node) {
  node.classList.add("ref-flash");
  setTimeout(() => node.classList.remove("ref-flash"), 2500);
}
