// One short line per settings value.
//
// Four readers need to say a value in a few words rather than show its control: a changed field
// says what it WAS, an override says what the pattern CARRIES, a closed "more" menu says what it
// holds, and the Library reads a pattern's settings to a person rather than dumping its JSON.
// One vocabulary for all four, so a value is spelled the same wherever it is summarised.

const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const BUDGET_WORDS = {
  max_turns: "turns", max_total_turns: "turns overall", max_wall_clock_min: "min",
  max_total_tokens: "tokens", max_cost: "$", max_subruns: "children",
  max_subrun_depth: "depth", ask_timeout_min: "min per ask",
};

const JUDGE_WORDS = { run: "the run proves it", you: "you decide", date: "on its date" };

const list = (xs, none = "none", max = 4) => {
  const items = (xs || []).map(String);
  if (!items.length) return none;
  return items.length > max
    ? `${items.slice(0, max).join(", ")} +${items.length - max}` : items.join(", ");
};

export function scheduleLine(spec) {
  const f = spec?.friendly || spec || {};
  const catchup = spec?.catchup === "run_once" ? " · runs once if a fire was missed" : "";
  switch (f.frequency) {
    case "disabled": return "disabled — no new runs";
    case "manual": case undefined: return "manual — only when started";
    case "hourly": return `hourly at :${String(f.minute ?? 0).padStart(2, "0")}${catchup}`;
    case "daily": return `daily at ${f.time}${catchup}`;
    case "weekly":
      return `weekly on ${(f.weekdays || []).map((d) => WEEKDAY[d] || d).join(", ") || "no day"} `
        + `at ${f.time}${catchup}`;
    case "monthly": return `monthly on day ${f.day} at ${f.time}${catchup}`;
    case "custom": return `custom cron ${f.cron}`;
    default: return String(f.frequency);
  }
}

export function gateLine(gate) {
  const checks = (gate?.checks || []).map((c) => String(c.kind || "?").replaceAll("_", " "));
  if (!checks.length) return "off — every scheduled fire starts a run";
  return `${gate.enabled ? "on" : "off"} · ${checks.length} check${checks.length === 1 ? "" : "s"}: `
    + `${list(checks)}${gate.enabled ? ` · answers within ${gate.timeout_s}s` : ""}`;
}

export function triggerLine(t) {
  const bits = [`cooldown ${t.cooldown_s ?? 0}s`];
  if (t.max_fires_per_day) bits.push(`${t.max_fires_per_day}/day`);
  return `${t.type} (${bits.join(", ")})`;
}

export function finishLineLine(doc) {
  const n = (doc?.outcomes || []).length;
  if (!n && !doc?.until) return "none — runs until you switch it off";
  const parts = [];
  if (n) parts.push(`${n} outcome${n === 1 ? "" : "s"}`);
  if (doc.until) parts.push(`stops after ${doc.until}`);
  return parts.join(" · ");
}

export function budgetLine(b) {
  return Object.entries(BUDGET_WORDS).filter(([k]) => b && k in b)
    .map(([k, w]) => (w === "$" ? `$${b[k] === -1 ? "∞" : b[k]}`
      : `${b[k] === -1 ? "∞" : b[k]} ${w}`)).join(" · ");
}

/** The value of one field in a few words. */
export function describe(key, v) {
  switch (key) {
    case "schedule": return scheduleLine(v);
    case "run_gate": return gateLine(v || {});
    case "triggers": return (v || []).length ? (v || []).map(triggerLine).join(" · ") : "no triggers";
    case "improve": return v ? "included in improvement" : "left out of improvement";
    case "permissions": return list(v, "no conduct permissions");
    case "capabilities": {
      const c = v || {};
      return [(c.actions || []).length ? `actions ${list(c.actions)}` : "",
        (c.utils || []).length ? `utils ${list(c.utils)}` : "",
        c.confirm ? `util approval: ${c.confirm}` : "",
        c.runs ? `previous runs: ${c.runs}` : "",
        c.reminders ? `reminders: ${c.reminders}` : "",
        // named where its dial is shown: only `global` writes a reminder anyone approves
        c.reminders === "global" && c.remind_confirm ? `reminder approval: ${c.remind_confirm}` : "",
        // named only when on: a routine without the task layer has nothing to say about it
        c.tasks === "on" ? "keeps tasks" : "",
      ].filter(Boolean).join(" · ") || "none";
    }
    case "rules": return (v || []).length ? `${v.length} rule${v.length === 1 ? "" : "s"}: ${list(v)}`
      : "no general rules";
    case "reminders": return list(v, "no shared reminders");
    case "grants": {
      const g = v || {};
      const secrets = Object.entries(g).filter(([k]) => k.startsWith("secret:"));
      const exposed = secrets.filter(([, x]) => x).map(([k]) => k.slice(7));
      const withheld = secrets.filter(([, x]) => !x).map(([k]) => k.slice(7));
      const declined = Object.entries(g).filter(([k, x]) => !k.startsWith("secret:") && x === false);
      return [exposed.length ? `exposes ${list(exposed)}` : "",
        withheld.length ? `withholds ${list(withheld)}` : "",
        declined.length ? `${declined.length} declined` : ""].filter(Boolean).join(" · ")
        || "nothing decided — asks on first use";
    }
    case "finish_line": return finishLineLine(v);
    case "budgets": return budgetLine(v || {});
    case "keep_runs": return `keeps ${v} runs`;
    case "fs_read_roots": case "fs_write_roots": return list(v, "none", 3);
    case "connections": return Object.entries(v || {}).map(([p, a]) => `${p}: ${a}`).join(", ")
      || "no accounts bound";
    case "machines": return list(v, "no machines");
    case "models": return Object.entries(v || {}).filter(([, m]) => m)
      .map(([k, m]) => `${k}: ${m}`).join(" · ") || "system default";
    case "deliberation": return String(v || "standard");
    // The escalation ladder (engine/ladder.py). `enabled` is the whole substance — a depth
    // nobody is watching under is noise — so the off state says only that.
    case "ladder": {
      const l = v || {};
      return l.enabled ? `supervised, at most ${l.max_depth ?? 3} rungs` : "not supervised";
    }
    case "ladder_rung_height": return `a rung every ${v ?? 20} turns`;
    // null is not "unset": the engine DERIVES it (n // 2 + 1, floored at 4), so saying "—" here
    // would read as a knob nobody set rather than one the engine computes.
    case "oversight_turns": return v ? `${v} turns per rung` : "rung budget derived from the interval";
    case "tags": return list(v, "no tags");
    case "hub_tab": return v ? String(v) : "no hub tab";
    default: return v === null || v === undefined || v === "" ? "—" : String(v);
  }
}

export { JUDGE_WORDS };

/** Like `describe`, but a list (`shape` "set" in the payload's `meta`) is spelled out in full —
 *  for a reader with room for it. */
export function describeFull(key, v, shape) {
  if (shape === "set") return (v || []).length ? v.join(", ") : describe(key, v);
  if (key === "finish_line" && (v?.outcomes || []).length) {
    return `${v.outcomes.map((o) => `${o.text} (${JUDGE_WORDS[o.judge] || o.judge}${o.date ? ` ${o.date}` : ""})`)
      .join("; ")}${v.until ? ` · stops after ${v.until}` : ""}`;
  }
  return describe(key, v);
}
