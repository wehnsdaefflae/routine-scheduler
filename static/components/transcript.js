// Shared transcript event renderer — one function per event type, keyed off the
// transcript JSONL contract (assistant_action/observation pairs render as one turn box).

import { el, fmtTokens } from "/static/util.js";

const BRIEF_FIELD = { shell: "command", read_file: "path", write_file: "path",
                      llm: "prompt", subinstruction: "label", ask_user: "question",
                      finish: "status" };

export function createTranscript(container) {
  const root = el("div", { class: "transcript" });
  container.append(root);
  let openTurn = null; // the turn box awaiting its observation

  function addTurn(ev) {
    const a = ev.payload;
    const brief = String(a[BRIEF_FIELD[a.kind]] ?? "").slice(0, 200);
    const turn = el("div", { class: "turn" },
      el("div", { class: "say" }, el("span", { class: "n" }, `turn ${ev.turn ?? "?"}`), a.say || ""),
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

  function addObservation(ev) {
    const o = ev.payload;
    let text;
    if (o.kind === "shell") {
      text = o.rejected ? `REJECTED: ${(o.problems || []).join("; ")}`
        : `exit ${o.exit} (${o.duration_s}s)\n${o.stdout || ""}${o.stderr ? `\n[stderr] ${o.stderr}` : ""}`;
    } else if (o.kind === "read_file") {
      text = o.error || o.content || "";
    } else if (o.kind === "llm") {
      text = o.error || o.reply || "";
    } else if (o.kind === "write_file") {
      text = o.error || `wrote ${o.bytes} bytes → ${o.path}`;
    } else if (o.kind === "subinstruction") {
      text = `${o.status} after ${o.turns} turns\n${o.summary || ""}`;
    } else if (o.kind === "ask_user") {
      text = o.answered ? `answered: ${o.answer}` : o.timed_out ? "timed out → deferred" : "filed as deferred";
    } else if (o.kind === "finish" && o.rejected) {
      text = "finish REJECTED — no action had been executed yet (fabrication guard)";
    } else {
      text = JSON.stringify(o, null, 1);
    }
    const obs = el("div", { class: "obs" }, text);
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
      `↰ subrun ${ev.payload.n} "${ev.payload.label}" ${ev.payload.status} — ${ev.payload.turns} turns, ${fmtTokens(ev.payload.usage)}`),
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
    clear() { root.innerHTML = ""; openTurn = null; },
  };
}
