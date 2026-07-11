// Python syntax highlighting for the library editors (workflows + utils are Python; the
// runtime never sees this — display only). No external assets: a small tokenizer building
// DOM spans via textContent, keeping util.js's "no HTML pathway" invariant. The editor is
// the classic overlay: a highlighted <pre> under a transparent-text <textarea>, scroll-synced,
// so editing behavior (undo, selection, IME) stays 100% native.

import { el } from "/static/util.js";

const CONST = new Set(["True", "False", "None"]);
const KW = new Set(("and as assert async await break class continue def del elif else except "
  + "finally for from global if import in is lambda nonlocal not or pass raise return try "
  + "while with yield").split(" "));

// Alternation order matters: comments win the rest of the line, triple-quoted strings win
// over single-quoted, strings (with their r/b/f prefixes) win over bare words. Unterminated
// strings highlight to end-of-line/file so mid-edit states look sane.
const TOK = new RegExp([
  "(#[^\\n]*)",                                                                    // 1 comment
  "([rRbBuUfF]{0,2}(?:'''[\\s\\S]*?(?:'''|$)|\"\"\"[\\s\\S]*?(?:\"\"\"|$)))",      // 2 triple string
  "([rRbBuUfF]{0,2}(?:'(?:\\\\.|[^'\\\\\\n])*'?|\"(?:\\\\.|[^\"\\\\\\n])*\"?))",   // 3 string
  "(@[\\w.]+)",                                                                    // 4 decorator
  "(\\b(?:0[xXoObB][\\da-fA-F_]+|\\d[\\d_]*(?:\\.[\\d_]*)?(?:[eE][+-]?\\d+)?[jJ]?)\\b)", // 5 number
  "([A-Za-z_]\\w*)",                                                               // 6 word
].join("|"), "g");

export function highlightPython(src) {
  const frag = document.createDocumentFragment();
  let last = 0;
  let pendingDef = false;   // the word right after `def`/`class` is the defined name
  for (const m of src.matchAll(TOK)) {
    if (m.index > last) frag.append(src.slice(last, m.index));
    last = m.index + m[0].length;
    const [full, com, tstr, str, dec, num, word] = m;
    let cls = null;
    if (com) cls = "tok-com";
    else if (tstr || str) cls = "tok-str";
    else if (dec) cls = "tok-dec";
    else if (num) cls = "tok-num";
    else if (word) {
      if (pendingDef) cls = "tok-def";
      else if (CONST.has(word)) cls = "tok-const";
      else if (KW.has(word)) cls = "tok-kw";
    }
    pendingDef = word === "def" || word === "class";
    if (cls) frag.append(el("span", { class: cls }, full));
    else frag.append(full);
  }
  if (last < src.length) frag.append(src.slice(last));
  return frag;
}

// A drop-in editor: same `.value` contract as a textarea. lang "python" gets the highlight
// overlay; anything else stays a plain textarea (fragments are markdown — no highlighter).
export function codeEditor(content, { lang = null, minHeight = 360 } = {}) {
  const ta = el("textarea", { class: "code", spellcheck: "false",
    style: `min-height:${minHeight}px` });
  ta.value = content || "";
  if (lang !== "python") {
    return { node: ta, get value() { return ta.value; }, set value(v) { ta.value = v; } };
  }
  ta.setAttribute("wrap", "off");   // both layers must break lines identically: never wrap
  const code = el("code", {});
  const pre = el("pre", { class: "hl", "aria-hidden": "true" }, code);
  const paint = () => {
    // trailing newline so the pre never ends up one line shorter than the textarea
    code.replaceChildren(highlightPython(ta.value), "\n");
  };
  const sync = () => { pre.scrollTop = ta.scrollTop; pre.scrollLeft = ta.scrollLeft; };
  ta.addEventListener("input", () => { paint(); sync(); });
  ta.addEventListener("scroll", sync);
  paint();
  return {
    node: el("div", { class: "code-editor" }, pre, ta),
    get value() { return ta.value; },
    set value(v) { ta.value = v; paint(); sync(); },
  };
}
