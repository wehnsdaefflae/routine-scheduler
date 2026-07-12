// Minimal markdown → DOM for MODEL-AUTHORED prose (say, summaries, llm replies, questions).
// This is the one sanctioned innerHTML pathway in the UI (util.js el() stays textContent-only):
// the raw text is HTML-escaped FIRST, so only the transforms below can introduce markup, and
// link hrefs are scheme-whitelisted — model/user text can never become live HTML.
//
// Deliberately small: **bold**, *italic*, `code`, ``` fences ```, [text](http…), # ## ###
// headings, - / * / 1. lists, paragraphs with line breaks. Anything else stays literal text.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ESC[c]);

// Inline transforms on an ALREADY-ESCAPED line. Code spans are pulled out first so bold/italic
// markers inside them stay literal.
function inline(s) {
  const codes = [];
  s = s.replace(/`([^`\n]+)`/g, (_, c) => `\x00${codes.push(c) - 1}\x00`);
  s = s
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\s][^*\n]*?)\*/g, "<em>$1</em>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+|mailto:[^)\s]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return s.replace(/\x00(\d+)\x00/g, (_, i) => `<code>${codes[+i]}</code>`);
}

export function mdToHtml(text) {
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let para = [], list = null, fence = null;
  const flushPara = () => { if (para.length) { out.push(`<p>${para.join("<br>")}</p>`); para = []; } };
  const flushList = () => {
    if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${i}</li>`).join("")}</${list.tag}>`); list = null; }
  };
  const item = (tag, body) => {
    flushPara();
    if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
    list.items.push(inline(esc(body)));
  };
  for (const raw of lines) {
    if (fence) {
      if (raw.trim().startsWith("```")) { out.push(`<pre><code>${fence.join("\n")}</code></pre>`); fence = null; }
      else fence.push(esc(raw));
      continue;
    }
    const t = raw.trim();
    let m;
    if (t.startsWith("```")) { flushPara(); flushList(); fence = []; }
    else if (!t) { flushPara(); flushList(); }
    else if ((m = /^(#{1,3})\s+(.*)$/.exec(t))) {
      flushPara(); flushList();
      out.push(`<h${m[1].length + 2}>${inline(esc(m[2]))}</h${m[1].length + 2}>`); // # → h3 … ### → h5
    } else if ((m = /^[-*]\s+(.*)$/.exec(t))) item("ul", m[1]);
    else if ((m = /^\d+[.)]\s+(.*)$/.exec(t))) item("ol", m[1]);
    else { flushList(); para.push(inline(esc(t))); }
  }
  if (fence) out.push(`<pre><code>${fence.join("\n")}</code></pre>`); // unterminated fence
  flushPara(); flushList();
  return out.join("");
}

// Block-level render (summaries, llm replies): a div.md (extra classes via cls).
export function md(text, cls = "md") {
  const div = document.createElement("div");
  div.className = cls;
  div.innerHTML = mdToHtml(text);
  return div;
}

// Inline-level render (say narration, questions): a span — no paragraphs/lists, newlines
// become <br>, so it sits inside flex row layouts without forcing a block.
export function mdInline(text) {
  const span = document.createElement("span");
  span.className = "mdi";
  span.innerHTML = String(text ?? "").split("\n").map((l) => inline(esc(l))).join("<br>");
  return span;
}
