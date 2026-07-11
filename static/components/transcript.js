// Shared transcript event renderer — one function per event type, keyed off the
// transcript JSONL contract (assistant_action/observation pairs render as one turn box).

import { el, fmtTime, fmtTokens } from "/static/util.js";

const BRIEF_FIELD = { util: "name", write_util: "name", read_file: "path", write_file: "path",
                      llm: "prompt", spawn: "label", kill: "n", wait: "n",
                      ask_user: "question", finish: "status" };

export function createTranscript(container) {
  const root = el("div", { class: "transcript" });
  container.append(root);
  let openTurn = null; // the turn box awaiting its observation

  function addTurn(ev) {
    const a = ev.payload;
    const brief = String(a[BRIEF_FIELD[a.kind]] ?? "").slice(0, 200);
    const turn = el("div", { class: "turn" },
      el("div", { class: "say" },
        el("span", { class: "n" }, `turn ${ev.turn ?? "?"}`),
        ev.ts ? el("span", { class: "ts", title: ev.ts }, fmtTime(ev.ts)) : null,
        el("span", { class: "saytext" }, a.say || "")),
      el("div", { class: "act" },
        el("span", {}, a.kind),
        el("span", { class: "muted" }, brief),
        ev.usage ? el("span", { class: "muted", style: "margin-left:auto" }, fmtTokens(ev.usage)) : null),
      el("details", { class: "raw" }, el("summary", {}, "action json"),
        el("pre", {}, JSON.stringify(a, null, 1))));
    root.append(turn);
    openTurn = a.kind === "finish" ? null : turn;
    return turn;
  }

  // Tool/observation return values are collapsed by default (expandable). The summary carries the
  // first line, so short one-line results stay fully readable without expanding.
  function obsBody(kind, text) {
    const firstLine = (text.split("\n")[0] || "").slice(0, 120);
    const more = text.length > firstLine.length;
    return el("details", { class: "obs-collapse" },
      el("summary", {}, `result — ${firstLine}${more ? " …" : ""}`),
      el("div", { class: "obs" }, text));
  }

  function addObservation(ev) {
    const o = ev.payload;
    let text;
    if (o.kind === "util") {
      text = o.missing ? `util "${o.name}" does not exist (available: ${(o.available || []).join(", ")})`
        : o.listing != null ? `util catalog\n${o.listing}`
        : `${o.name} → exit ${o.exit}\n${o.stdout || ""}${o.stderr ? `\n[stderr] ${o.stderr}` : ""}`;
    } else if (o.kind === "write_util") {
      text = o.pending_approval ? `write_util "${o.name}": awaiting user approval`
        : o.declined ? `write_util "${o.name}": declined`
        : o.selftest_ok ? `write_util "${o.name}": selftest passed, committed`
        : `write_util "${o.name}": selftest FAILED\n${o.output || ""}`;
    } else if (o.kind === "read_file") {
      text = o.error || o.content || "";
    } else if (o.kind === "llm") {
      text = o.error || o.reply || "";
    } else if (o.kind === "write_file") {
      text = o.error || `wrote ${o.bytes} bytes → ${o.path}`;
    } else if (o.kind === "spawn") {
      text = o.rejected ? `spawn REJECTED: ${o.reason}` :
        `sub-workflow #${o.n} "${o.label}" started (${o.workflow}) — running in parallel`;
    } else if (o.kind === "subruns") {
      text = (o.rows || []).map((r) =>
        `#${r.n} "${r.label}" [${r.workflow}] ${r.state} · ${r.turns} turns · ${r.elapsed_s}s`)
        .join("\n") || "no sub-workflows";
    } else if (o.kind === "kill") {
      text = o.error || `sub-workflow #${o.n} ${o.already_finished ? "had already finished" : "terminated"}`;
    } else if (o.kind === "wait") {
      text = o.error || ((o.finished || []).map((f) =>
        `#${f.n} "${f.label}" finished (${f.status}, ${f.turns} turns):\n${f.summary}`)
        .join("\n\n") || (o.timed_out ? "wait timed out" : "nothing new finished"));
    } else if (o.kind === "ask_user") {
      text = o.answered ? `answered: ${o.answer}` : o.timed_out ? "timed out → deferred" : "filed as deferred";
    } else if (o.kind === "finish" && o.rejected) {
      text = "finish REJECTED — no action had been executed yet (fabrication guard)";
    } else {
      text = JSON.stringify(o, null, 1);
    }
    const obs = obsBody(o.kind, text);
    if (openTurn) { openTurn.append(obs); openTurn = null; }
    else root.append(el("div", { class: "turn" }, obs));
  }

  const SIMPLE = {
    user_injection: (ev) => el("div", { class: "ev injection" }, `\u{1F4E8} injected: ${ev.payload.text}`),
    question: (ev) => el("div", { class: "ev question" },
      `❓ [${ev.payload.mode}] ${ev.payload.question}` +
      (ev.payload.options?.length ? ` — options: ${ev.payload.options.join(" | ")}` : "")),
    answer: (ev) => el("div", { class: "ev answer" }, `✅ answer (${ev.payload.source}): ${ev.payload.text}`),
    error: (ev) => el("div", { class: "ev error" },
      `error (${ev.payload.where}${ev.payload.attempt ? `, attempt ${ev.payload.attempt}` : ""}): ${ev.payload.message}`),
    compaction: (ev) => el("div", { class: "ev compaction" },
      `— context compacted: ${ev.payload.before_chars} → ${ev.payload.after_chars} chars —`),
    subrun_start: (ev) => el("div", { class: "ev subrun" },
      `↳ subrun ${ev.payload.n} "${ev.payload.label}" started (depth ${ev.payload.depth})`),
    subrun_end: (ev) => el("div", { class: "ev subrun" },
      el("details", { class: "obs-collapse" },
        el("summary", {},
          `↰ subrun ${ev.payload.n} "${ev.payload.label}" ${ev.payload.status} — ${ev.payload.turns} turns, ${fmtTokens(ev.payload.usage)}`),
        el("div", { class: "obs" }, ev.payload.summary || "(no summary)"))),
    header: (ev) => el("div", { class: "ev system" },
      `run ${ev.run_id} · ${ev.orchestrator?.endpoint}:${ev.orchestrator?.model} · workflow ${ev.workflow?.slug || "?"}`),
  };

  return {
    add(ev) {
      if (ev.type === "assistant_action") return void addTurn(ev);
      if (ev.type === "observation") return void addObservation(ev);
      if (ev.type === "finish") {
        const p = ev.payload;
        root.append(el("div", { class: `finish-banner ${p.status}` },
          el("strong", {}, `finish: ${p.status}`),
          el("div", { class: "mt", style: "margin-top:6px" }, p.summary || ""),
          el("div", { class: "muted", style: "margin-top:6px" },
            `${ev.turns ?? "?"} turns · ${fmtTokens(ev.usage_total)}`)));
        return;
      }
      const renderer = SIMPLE[ev.type];
      if (renderer) root.append(renderer(ev));
    },
    clear() { root.replaceChildren(); openTurn = null; },
  };
}
