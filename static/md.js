// Minimal markdown → DOM for MODEL-AUTHORED prose (say, summaries, llm replies, questions).
// This is the one sanctioned innerHTML pathway in the UI (util.js el() stays textContent-only):
// the raw text is HTML-escaped FIRST, so only the transforms below can introduce markup, and
// link hrefs are scheme-whitelisted — model/user text can never become live HTML.
//
// Deliberately small: **bold**, *italic*, `code`, ``` fences ```, [text](http…), # ## ###
// headings, - / * / 1. lists, GFM pipe tables (header + |---| separator; a malformed table
// stays literal text), > blockquotes, paragraphs with line breaks. Anything else stays
// literal text. mdInline() (say narration, questions) keeps the inline-only subset — no
// block elements, so tables/quotes render only on block surfaces (md()).

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

// One table row → trimmed cells: one optional leading/trailing pipe stripped, \| stays a
// literal pipe inside a cell. Runs on the RAW line — each cell is escaped at render.
function cells(line) {
  const out = [];
  let cur = "";
  const s = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && s[i + 1] === "|") { cur += "|"; i++; }
    else if (s[i] === "|") { out.push(cur.trim()); cur = ""; }
    else cur += s[i];
  }
  out.push(cur.trim());
  return out;
}

// The separator row under a table header: every cell `---` with optional `:` alignment
// markers, and (GFM) the same cell count as the header — anything else is not a table.
function alignments(line, count) {
  if (!/^\s*\|?[\s:|-]+\|?\s*$/.test(line) || !line.includes("-")) return null;
  const seps = cells(line);
  if (seps.length !== count || !seps.every((c) => /^:?-+:?$/.test(c))) return null;
  return seps.map((c) => (c.startsWith(":") && c.endsWith(":") ? "center"
    : c.endsWith(":") ? "right" : null));
}

export function mdToHtml(text, _depth = 0) {
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
  const cell = (tag, body, align) =>
    `<${tag}${align ? ` style="text-align:${align}"` : ""}>${inline(esc(body))}</${tag}>`;
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    if (fence) {
      if (raw.trim().startsWith("```")) { out.push(`<pre><code>${fence.join("\n")}</code></pre>`); fence = null; }
      else fence.push(esc(raw));
      continue;
    }
    const t = raw.trim();
    let m, aligns;
    if (t.startsWith("```")) { flushPara(); flushList(); fence = []; }
    else if (!t) { flushPara(); flushList(); }
    else if ((m = /^(#{1,3})\s+(.*)$/.exec(t))) {
      flushPara(); flushList();
      out.push(`<h${m[1].length + 2}>${inline(esc(m[2]))}</h${m[1].length + 2}>`); // # → h3 … ### → h5
    } else if (t.startsWith(">") && _depth < 4) {
      // blockquote: consecutive `>` lines, one marker stripped per line, body re-parsed
      // (nested quotes/lists/tables work); depth-capped so `>>>>…` can't recurse away
      flushPara(); flushList();
      const quote = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quote.push(lines[i].trim().replace(/^>\s?/, ""));
        i++;
      }
      i--;
      out.push(`<blockquote>${mdToHtml(quote.join("\n"), _depth + 1)}</blockquote>`);
    } else if (t.includes("|")
               && (aligns = alignments(lines[i + 1] ?? "", cells(raw).length)) !== null) {
      // GFM pipe table: header row + a matching |---| separator; body rows padded or
      // truncated to the header width. No valid separator → the lines stay literal text.
      flushPara(); flushList();
      const head = cells(raw);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        const r = cells(lines[i]).slice(0, head.length);
        while (r.length < head.length) r.push("");
        rows.push(r);
        i++;
      }
      i--;
      out.push('<div class="tablewrap"><table class="list"><thead><tr>'
        + head.map((h, c) => cell("th", h, aligns[c])).join("") + "</tr></thead><tbody>"
        + rows.map((r) => `<tr>${r.map((v, c) => cell("td", v, aligns[c])).join("")}</tr>`).join("")
        + "</tbody></table></div>");
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
