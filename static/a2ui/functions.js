// The Basic Catalog's FOURTEEN FUNCTIONS — D310 step 3, the catalog's computations.
//
// A2UI never sends executable code: an agent that wants a formatted date, a validated field or a
// condition says so by NAMING a function the catalog declares, and the renderer runs its own
// implementation. So this table is the whole of what `{"@call": …}` and every `@checks` rule can
// do. It is a module of its own because it is pure — values in, strings and `ValidationResult`s
// out, no DOM, no surface, no component — and the two members with a GRAMMAR rather than a
// computation (`regex`'s dialect, `formatString`'s `${…}`) live in `expr.js` beside it, so this
// file stays a table you read to learn what a function does.
//
// WHAT THE CATALOG DECLARES, and therefore what is here:
//   required · regex · length · numeric · email      — validation, returning a ValidationResult
//   formatString · formatNumber · formatCurrency · formatDate · pluralize  — formatting
//   and · or · not                                   — boolean combinators
//   openUrl                                          — the one function needing user activation
//
// SPEC DECISIONS read off the v1.0 specification rather than assumed:
//   - a validator returns `{valid: true}` or `{valid: false, message, severity: "error"}`, and
//     `DynamicBoolean` coercion reads `.valid` — which is what lets ONE property type carry both
//     a check rule and a plain condition;
//   - `openUrl` declares `requiresUserActivation: true`, so it MUST refuse to run from a passive
//     binding or a CheckRule condition and report `EXPRESSION_ERROR`. The refusal is here
//     because the catalog's own declaration is what demands it;
//   - `regex` over a value longer than 4,096 code points does not match and reports NO error:
//     the field is invalid, not broken, so a pasted document fails the field rather than the
//     surface;
//   - non-strings interpolate as the spec fixes them — numbers and booleans standard, null and
//     undefined as "", objects and arrays as JSON (`asText`, in `surface.js`).

import { A2uiExpressionError, dialectRegex, evaluate, expressions } from "./expr.js";
import { asBool, asText, readPointer } from "./surface.js";

//: A validator's answer. `valid: true` carries nothing else — the spec's shape, and the reason
//: `asBool` can read a check rule and a boolean binding through one property type.
const ok = () => ({ valid: true });
const bad = (message) => ({ valid: false, message, severity: "error" });

//: `formatDate`'s pattern tokens, longest first so `yyyy` is never read as two `yy`. Local time,
//: because a surface is drawn for a person sitting in front of it.
const DATE_TOKENS = [
  ["yyyy", (d) => String(d.getFullYear()).padStart(4, "0")],
  ["yy", (d) => String(d.getFullYear() % 100).padStart(2, "0")],
  ["MM", (d) => String(d.getMonth() + 1).padStart(2, "0")],
  ["dd", (d) => String(d.getDate()).padStart(2, "0")],
  ["HH", (d) => String(d.getHours()).padStart(2, "0")],
  ["mm", (d) => String(d.getMinutes()).padStart(2, "0")],
  ["ss", (d) => String(d.getSeconds()).padStart(2, "0")],
];

//: Every function the Basic Catalog declares, by name. Each takes its resolved `args` object and
//: the evaluation `context` (the data model, the scope, and a `call` back into this table for
//: the nested calls `formatString` allows).
export const BASIC_FUNCTIONS = {
  required: ({ value }) => {
    const empty = value === null || value === undefined || value === ""
      || (Array.isArray(value) && value.length === 0);
    return empty ? bad("required") : ok();
  },

  regex: ({ value, pattern }) => {
    const text = asText(value);
    if ([...text].length > 4096) return bad("too long to match");
    return dialectRegex(pattern).test(text) ? ok() : bad("does not match");
  },

  length: ({ value, min, max }) => {
    const n = [...asText(value)].length;
    if (min !== undefined && min !== null && n < Number(min)) return bad(`shorter than ${min}`);
    if (max !== undefined && max !== null && n > Number(max)) return bad(`longer than ${max}`);
    return ok();
  },

  numeric: ({ value, min, max }) => {
    const n = Number(value);
    if (value === "" || value === null || value === undefined || Number.isNaN(n)) {
      return bad("not a number");
    }
    if (min !== undefined && min !== null && n < Number(min)) return bad(`below ${min}`);
    if (max !== undefined && max !== null && n > Number(max)) return bad(`above ${max}`);
    return ok();
  },

  // Deliberately narrow and local: one @, a non-empty local part, a dotted domain, no spaces.
  // An address is validated for the shape a person mistypes, never for RFC 5322 completeness.
  email: ({ value }) => (/^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/u.test(asText(value))
    ? ok() : bad("not an email address")),

  formatString: ({ value }, context) => expressions(asText(value))
    .map((part) => ("text" in part ? part.text : asText(evaluate(part.expression, context))))
    .join(""),

  formatNumber: ({ value, decimals, grouping }) => {
    const n = Number(value);
    if (Number.isNaN(n)) return "";
    return n.toLocaleString(undefined, {
      minimumFractionDigits: decimals ?? undefined,
      maximumFractionDigits: decimals ?? undefined,
      useGrouping: grouping ?? true,
    });
  },

  formatCurrency: ({ value, currency, decimals, grouping }) => {
    const n = Number(value);
    if (Number.isNaN(n)) return "";
    return n.toLocaleString(undefined, {
      style: "currency",
      currency: String(currency),
      minimumFractionDigits: decimals ?? undefined,
      maximumFractionDigits: decimals ?? undefined,
      useGrouping: grouping ?? true,
    });
  },

  formatDate: ({ value, format }) => {
    const when = typeof value === "number" ? new Date(value) : new Date(asText(value));
    if (Number.isNaN(when.getTime())) return "";
    let out = String(format);
    for (const [token, render] of DATE_TOKENS) out = out.split(token).join(render(when));
    return out;
  },

  // CLDR categories, and `other` is required by the catalog precisely because it is the fallback
  // every locale has. `two`/`few`/`many` are used when given, so an agent writing for a locale
  // that has them is not silently flattened to English's two forms.
  pluralize: (args) => {
    const n = Number(args.value);
    const category = Number.isNaN(n) ? "other" : new Intl.PluralRules(undefined).select(n);
    return asText(args[category] ?? args.other);
  },

  openUrl: ({ url }, context) => {
    if (!context.activated) {
      throw new A2uiExpressionError(
        "openUrl requires user activation: it may not run from a binding or a check rule");
    }
    context.openUrl(asText(url));
    return undefined;
  },

  and: ({ values }) => (Array.isArray(values) ? values.every((v) => asBool(v)) : asBool(values)),
  or: ({ values }) => (Array.isArray(values) ? values.some((v) => asBool(v)) : asBool(values)),
  not: ({ value }) => !asBool(value),
};

//: The one entry point a renderer needs: call `name` with `args`, in `context`. An unknown name
//: is an expression error rather than `undefined` — a surface drawn from a function the renderer
//: does not have is a surface the agent cannot reason about.
export function callBasicFunction(name, args, context = {}) {
  const fn = BASIC_FUNCTIONS[name];
  if (!fn) throw new A2uiExpressionError(`no function "${name}" in the basic catalog`);
  const full = {
    model: context.model ?? {},
    scope: context.scope ?? "",
    activated: Boolean(context.activated),
    openUrl: context.openUrl ?? ((url) => window.open(url, "_blank", "noopener")),
  };
  full.call = (inner, innerArgs) => callBasicFunction(inner, innerArgs, full);
  return fn(args ?? {}, full);
}

//: A `@checks` list as ONE answer, which is what a field shows: the first failing rule, coerced
//: the way the spec fixes it — a `true` condition becomes `{valid: true}`, a `false` one takes
//: the rule's own message, and a ValidationResult keeps its message with the rule's as fallback.
export function evaluateChecks(checks, resolve) {
  for (const rule of checks ?? []) {
    const outcome = resolve(rule.condition);
    if (outcome && typeof outcome === "object" && typeof outcome.valid === "boolean") {
      if (!outcome.valid) {
        return {
          valid: false,
          message: outcome.message ?? rule.message ?? "invalid",
          severity: outcome.severity ?? "error",
        };
      }
      continue;
    }
    if (!asBool(outcome)) return bad(rule.message ?? "invalid");
  }
  return ok();
}

export { A2uiExpressionError, readPointer };
