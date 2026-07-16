// Audit reference tokens (F63, D14) in rendered prose become links to the card they name
// on the Audit page (#/audit?focus=F63) — clicking one anywhere in the console lands on,
// scrolls to, and flashes that card. linkifyRefs walks TEXT nodes only, so it composes
// with mdInline output and never rewrites existing links, code, or form controls. Apply it
// ONLY to prose that is the audit's own voice (the Audit page, meta-badged decisions) —
// on arbitrary text a bare "D1" is a false positive.

const REF_RE = /\b([FD]\d{1,3})\b/g;

export function linkifyRefs(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement?.closest("a, code, pre, button, select, textarea, summary")
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const hits = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    REF_RE.lastIndex = 0;
    if (REF_RE.test(n.nodeValue)) hits.push(n);
  }
  for (const n of hits) {
    const frag = document.createDocumentFragment();
    let last = 0;
    REF_RE.lastIndex = 0;
    for (const m of n.nodeValue.matchAll(REF_RE)) {
      frag.append(n.nodeValue.slice(last, m.index));
      const a = document.createElement("a");
      a.className = "ref-link";
      a.href = `#/audit?focus=${m[1]}`;
      a.title = `jump to ${m[1]} on the Audit page`;
      a.textContent = m[1];
      frag.append(a);
      last = m.index + m[1].length;
    }
    frag.append(n.nodeValue.slice(last));
    n.replaceWith(frag);
  }
  return root;
}

// The landing half of a ref link: scroll the named card (id="ref-<id>") into view and
// flash it. Returns false when the id has no card (e.g. a finding dropped from the report).
export function focusRef(id) {
  const target = document.getElementById(`ref-${id}`);
  if (!target) return false;
  target.scrollIntoView({ block: "center" });
  target.classList.add("ref-flash");
  setTimeout(() => target.classList.remove("ref-flash"), 2500);
  return true;
}
