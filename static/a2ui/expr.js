// A2UI's two little LANGUAGES — D310 step 3, the parsing layer under the function library.
//
// Two of the Basic Catalog's functions are not computations but PARSERS, and they are the only
// parts of the catalog with a grammar of their own:
//   - `regex` evaluates a pattern in the A2UI regex DIALECT: a portable subset of ECMAScript in
//     Unicode mode, with a static safety check that refuses the shapes whose matching is
//     super-linear. A pattern is rejected on its SOURCE TEXT, before `RegExp` ever sees it,
//     because this is a portability contract rather than a parse error — a backreference
//     compiles perfectly well in V8 and would simply work, which is exactly the problem.
//   - `formatString` interpolates `${…}` expressions, each a JSON Pointer or a function call
//     recognised by its parentheses, with literal or NESTED-expression arguments. Nesting is
//     why this cannot be a regex: `${upper(${now()})}` is one expression, and brace depth is
//     the only way to know that.
//
// They live apart from `functions.js` because that file is a TABLE — fourteen names, each a few
// lines, read when you want to know what a function does — while this is two parsers read when
// you want to know what the grammar admits. Changing the safety rules touches nothing in the
// table, and adding a function touches nothing here.

import { resolveInScope } from "./surface.js";

//: Thrown for a local evaluation the protocol forbids or cannot perform. The spec names the code
//: it carries, and the renderer reports it rather than drawing a half-evaluated property.
export class A2uiExpressionError extends Error {
  constructor(message) {
    super(message);
    this.name = "A2uiExpressionError";
    this.code = "EXPRESSION_ERROR";
  }
}

// ---- the regex dialect ------------------------------------------------------------------------

//: Constructs the dialect forbids, each paired with what it is, matched as source text.
const FORBIDDEN = [
  [/\\[1-9]/, "backreferences"],
  [/\\k</, "named backreferences"],
  [/\(\?<[=!]/, "lookbehind"],
  [/\(\?</, "named groups"],
  [/\\[pP]\{/, "unicode property escapes"],
  [/\\c[A-Za-z]/, "control escapes"],
  [/\\0\d/, "legacy octal escapes"],
  [/\(\?[a-zA-Z]+[:)]/, "inline flags"],
];

//: The first characters an alternation branch can begin with, as a set of literal code points.
//: Approximate ON PURPOSE and only in the safe direction: a branch opening with a class or a
//: group contributes nothing, so two such branches are never *claimed* to overlap. The check
//: refuses the patterns that are obviously super-linear; it does not decide the general case.
function firstChars(branch) {
  const text = branch.replace(/^\((\?:)?/, "");
  const first = text[0];
  if (!first || "[(.\\*+?{|".includes(first)) return new Set();
  return new Set([first]);
}

//: The spec's static safety check: four rules, all about a quantifier that can be entered many
//: times over text another quantifier can already consume. Returns the reason to reject, or "".
function unsafeReason(pattern) {
  if ([...pattern].length > 256) return "longer than 256 code points";
  // Rule 2: a counted quantifier above 1 applied to a group holding an unbounded one.
  const nested = /\((?:\?:)?[^()]*[*+][^()]*\)(?:\{(\d+),?(\d*)\}|[*+])/.exec(pattern);
  if (nested) {
    const upper = nested[2] === "" ? Number(nested[1] || 2) : Number(nested[2]);
    if (!nested[1] || upper > 1) return "a quantifier over an unbounded subexpression";
  }
  // Rule 3: an unbounded quantifier over an alternation whose branches share a first character.
  for (const group of pattern.match(/\((?:\?:)?[^()]*\|[^()]*\)[*+]/g) ?? []) {
    const branches = group.replace(/^\((?:\?:)?/, "").replace(/\)[*+]$/, "").split("|");
    for (let i = 0; i < branches.length; i += 1) {
      for (let j = i + 1; j < branches.length; j += 1) {
        for (const c of firstChars(branches[i])) {
          if (firstChars(branches[j]).has(c)) return "an alternation with overlapping branches";
        }
      }
    }
  }
  // Rule 4: two adjacent unbounded atoms over overlapping character sets (`\w+\w+`, `a*a*`).
  const adjacent = /(\\?.|\[[^\]]*\])[*+](\\?.|\[[^\]]*\])[*+]/.exec(pattern);
  if (adjacent && adjacent[1] === adjacent[2]) return "two adjacent unbounded quantifiers";
  return "";
}

//: A pattern of the dialect as a `RegExp`, or an expression error naming WHY. Unicode mode and no
//: other flag, which IS the dialect: case-sensitive, no multiline, search semantics.
export function dialectRegex(pattern) {
  const source = String(pattern ?? "");
  for (const [probe, what] of FORBIDDEN) {
    if (probe.test(source)) throw new A2uiExpressionError(`regex: ${what} are not in the dialect`);
  }
  const unsafe = unsafeReason(source);
  if (unsafe) throw new A2uiExpressionError(`regex: rejected — ${unsafe}`);
  try {
    return new RegExp(source, "u");
  } catch (error) {
    throw new A2uiExpressionError(`regex: ${error.message}`);
  }
}

// ---- formatString's interpolation grammar -----------------------------------------------------

//: Split `${…}` expressions out of a template, respecting NESTING: `${upper(${now()})}` is one
//: expression, not a truncated one, so brace depth is counted rather than matched by regex.
export function expressions(template) {
  const parts = [];
  let plain = "";
  for (let i = 0; i < template.length; i += 1) {
    if (template[i] === "$" && template[i + 1] === "{") {
      let depth = 1;
      let j = i + 2;
      for (; j < template.length && depth > 0; j += 1) {
        if (template[j] === "{") depth += 1;
        else if (template[j] === "}") depth -= 1;
      }
      if (depth === 0) {
        if (plain) parts.push({ text: plain });
        plain = "";
        parts.push({ expression: template.slice(i + 2, j - 1) });
        i = j - 1;
        continue;
      }
    }
    plain += template[i];
  }
  if (plain) parts.push({ text: plain });
  return parts;
}

//: One argument list of a call inside `${…}`, split on commas that are NOT inside a nested
//: expression or a quoted literal — `format:'a,b'` is ONE argument.
function splitArgs(text) {
  const out = [];
  let current = "";
  let depth = 0;
  let quote = "";
  for (const c of text) {
    if (quote) {
      current += c;
      if (c === quote) quote = "";
      continue;
    }
    if (c === "'" || c === '"') { quote = c; current += c; continue; }
    if (c === "{") depth += 1;
    if (c === "}") depth -= 1;
    if (c === "," && depth === 0) { out.push(current); current = ""; continue; }
    current += c;
  }
  if (current.trim()) out.push(current);
  return out;
}

//: An argument's value: a nested expression, a quoted string, a number, a boolean, or a pointer.
function literal(raw, context) {
  if (raw.startsWith("${") && raw.endsWith("}")) return evaluate(raw.slice(2, -1), context);
  if ((raw.startsWith("'") && raw.endsWith("'")) || (raw.startsWith('"') && raw.endsWith('"'))) {
    return raw.slice(1, -1);
  }
  if (/^-?\d+(\.\d+)?$/.test(raw)) return Number(raw);
  if (raw === "true" || raw === "false") return raw === "true";
  return resolveInScope(context.model, raw, context.scope);
}

//: What one `${…}` body is worth: a JSON Pointer, or a call recognised by its parentheses. The
//: call goes back through `context.call`, so the catalog — not this parser — decides what a name
//: means, and a nested call inside `${…}` resolves in the catalog the enclosing call resolved to.
export function evaluate(body, context) {
  const text = body.trim();
  const call = /^([A-Za-z@][\w.]*)\((.*)\)$/s.exec(text);
  if (!call) return resolveInScope(context.model, text, context.scope);
  const [, name, rawArgs] = call;
  const args = {};
  let positional = 0;
  for (const piece of splitArgs(rawArgs)) {
    const named = /^\s*([A-Za-z_]\w*)\s*:([\s\S]*)$/.exec(piece);
    const key = named ? named[1] : String(positional++);
    const raw = (named ? named[2] : piece).trim();
    args[key] = literal(raw, context);
  }
  return context.call(name, args);
}
