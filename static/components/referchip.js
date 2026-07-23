// The "refer to" composer chip — the messenger reply analog. A hover ↩ on any
// transcript/chat message primes it (via setRef); the send prepends the quoted
// reference line to the message text (transcript.js splitRef/referButton own the
// convention). One implementation for the run view and the conversation composer —
// the two copies had already started to drift on spacing classes.
import { el } from "/static/util.js";

export function referChip(focusEl, { className = "composer-ref" } = {}) {
  let pending = null;
  const refText = el("span", { class: "ref-text" });
  const refClear = el("button", { class: "btn small ghost", title: "drop the reference" }, "✕");
  const node = el("div", { class: className, hidden: true }, "↩ ", refText, refClear);
  const setRef = (r) => {
    pending = r;
    node.hidden = !r;
    if (r) { refText.textContent = `${r.label}: ${r.snippet}`; focusEl.focus(); }
  };
  refClear.onclick = () => setRef(null);
  return { node, setRef, get pending() { return pending; }, clear: () => setRef(null) };
}
