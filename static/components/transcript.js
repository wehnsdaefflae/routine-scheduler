// Shared transcript event renderer — one function per event type, keyed off the
// transcript JSONL contract (assistant_action/observation pairs render as one turn box).
//
// opts wire the conversation to the live system (all optional — omitted = plain rendering):
//   answer(qid, text)  — enables an inline answer form on DEFERRED question events, so a
//                        decision can be settled right where it was asked (blocking
//                        questions keep the run view's prominent panel).
//   loadSub(n, offset) — enables expanding a subrun's own conversation in place under its
//                        start/end lines; n may be a nested path like "2/1". Returns
//                        {events, offset}.
//   isLive()           — true while the run is live; expanded subruns keep polling.

import { md, mdInline } from "/static/md.js";
import { answerForm } from "/static/components/answerform.js";
import { el, fmtTime, fmtTokens } from "/static/util.js";

const BRIEF_FIELD = { util: "name", write_util: "name", read_file: "path", write_file: "path",
                      edit_file: "path", memory_read: "name", memory_write: "name",
                      llm: "prompt", spawn: "label", kill: "n", wait: "n",
                      ask_user: "question", finish: "status" };

export function createTranscript(container, opts = {}) {
  const root = el("div", { class: "transcript" });
  container.append(root);
  let openTurn = null; // the turn box awaiting its observation
  const qforms = new Map();   // qid -> { controls, created } for open inline answer forms

  function closeQuestion(qid, note) {
    const f = qforms.get(qid);
    if (!f) return;
    qforms.delete(qid);
    if (note) f.controls.replaceChildren(el("span", { class: "faint small" }, note));
    else f.controls.remove();
  }

  function questionNode(ev) {
    const p = ev.payload;
    const label = p.type === "util-approval" ? `${p.mode} · util approval` : p.mode;
    const head = el("span", {}, `❓ [${label}] `, mdInline(p.question),
      p.options?.length ? ` — options: ${p.options.join(" | ")}` : null,
      p.default ? el("span", { class: "faint" }, ` · without an answer: ${p.default}`) : null);
    // Inline answering: deferred questions used to be dead text here, answerable only on
    // the Decisions page. Blocking ones stay with the run view's panel (it handles dialog).
    if (!opts.answer || !p.qid || p.mode !== "deferred") return el("div", { class: "ev question" }, head);
    const form = answerForm(p, {
      placeholder: "answer here — or on the Decisions page… (Shift+Enter for a new line)",
      defaultLine: false,   // the head already states the default inline
      submitText: (text) => opts.answer(p.qid, text),
      onSuccess: (text) => closeQuestion(p.qid, `✅ answered: ${text} (queued for the next run)`),
    });
    qforms.set(p.qid, { controls: form.node, created: Date.now() });
    return el("div", { class: "ev question" }, el("div", {}, head), form.node);
  }

  // A subrun line that can unfold into the child's own conversation, fetched on first
  // expand (and re-polled while the run is live) — nested children expand recursively.
  function subrunNode(ev, head, extra) {
    const p = ev.payload;
    if (!opts.loadSub) {
      return extra ? el("details", { class: "obs-collapse" }, el("summary", {}, head), extra)
                   : el("span", {}, head);
    }
    const details = el("details", { class: "obs-collapse" },
      el("summary", {}, `${head} · conversation`), extra || null);
    let mounted = false;
    details.addEventListener("toggle", () => {
      if (!details.open || mounted) return;
      mounted = true;
      const box = el("div", { class: "subtranscript" });
      details.append(box);
      const sub = createTranscript(box, {
        loadSub: (m, o) => opts.loadSub(`${p.n}/${m}`, o), isLive: opts.isLive });
      let off = 0, pulling = false;
      const pull = async () => {
        if (pulling) return;
        pulling = true;
        try {
          const r = await opts.loadSub(String(p.n), off);
          off = r.offset;
          for (const e of r.events) sub.add(e);
        } catch { /* transient — the next poll retries */ }
        pulling = false;
      };
      pull();
      if (opts.isLive?.()) {
        const poll = setInterval(() => {
          if (!document.body.contains(details)) return void clearInterval(poll);
          if (!opts.isLive()) return void clearInterval(poll);
          if (details.open) pull();
        }, 3000);
      }
    });
    return details;
  }

  function addTurn(ev) {
    const a = ev.payload;
    // For utils, show the whole call inline (name + args) — a missing args array must be
    // visible at a glance, not one click deep in the action json.
    const brief = a.kind === "util"
      ? `${a.name ?? ""}${Array.isArray(a.args) && a.args.length ? " " + a.args.join(" ") : "  (no args)"}`.slice(0, 200)
      : a.kind === "read_file" && Array.isArray(a.paths)
      ? a.paths.join(", ").slice(0, 200)
      : String(a[BRIEF_FIELD[a.kind]] ?? "").slice(0, 200);
    const turn = el("div", { class: "turn" },
      el("div", { class: "say" },
        el("span", { class: "n" }, `turn ${ev.turn ?? "?"}`),
        ev.ts ? el("span", { class: "ts", title: ev.ts }, fmtTime(ev.ts)) : null,
        el("span", { class: "saytext" }, mdInline(a.say || ""))),
      el("div", { class: "act" },
        el("span", {}, a.kind),
        el("span", { class: "muted" }, brief),
        ev.usage ? el("span", { class: "muted", style: "margin-left:auto",
                              title: ev.usage.provider ? `served by ${ev.usage.provider}` : "" },
                    fmtTokens(ev.usage)) : null),
      el("details", { class: "raw" }, el("summary", {}, "action json"),
        el("pre", {}, JSON.stringify(a, null, 1))));
    root.append(turn);
    openTurn = a.kind === "finish" ? null : turn;
    return turn;
  }

  // Tool/observation return values are collapsed by default (expandable). The summary carries the
  // first line, so short one-line results stay fully readable without expanding. `rich` renders
  // the body as simple markdown (model-authored prose — llm replies); program output stays literal.
  function obsBody(kind, text, rich = false) {
    const firstLine = (text.split("\n")[0] || "").slice(0, 120);
    const more = text.length > firstLine.length;
    return el("details", { class: "obs-collapse" },
      el("summary", {}, `result — ${firstLine}${more ? " …" : ""}`),
      rich ? md(text, "obs md") : el("div", { class: "obs" }, text));
  }

  function addObservation(ev) {
    const o = ev.payload;
    let text;
    if (o.kind === "util") {
      text = o.missing ? `util "${o.target || o.name}" does not exist (available: ${(o.available || []).join(", ")})`
        : o.listing != null ? `util catalog\n${o.listing}`
        : o.source != null ? `source of "${o.target}"\n${o.source}`
        : `${o.name} → exit ${o.exit}\n${o.stdout || ""}${o.stderr ? `\n[stderr] ${o.stderr}` : ""}`
          + (o.usage ? `\n[usage] ${o.usage}` : "") + (o.hint ? `\n[hint] ${o.hint}` : "");
    } else if (o.kind === "write_util") {
      text = o.pending_approval ? `write_util "${o.name}": awaiting user approval`
        : o.declined ? `write_util "${o.name}": declined`
        : o.selftest_ok ? `write_util "${o.name}": selftest passed, committed`
        : `write_util "${o.name}": selftest FAILED\n${o.output || ""}`;
    } else if (o.kind === "read_file") {
      text = o.files  // batched multi-path read: one section per file
        ? o.files.map((f) => f.error ? `--- ${f.path} FAILED: ${f.error}`
                                     : `--- ${f.path} (lines ${f.start_line}-${f.end_line} of ${f.total_lines}) ---\n${f.content}`)
            .join("\n\n")
        : o.error || o.content || "";
    } else if (o.kind === "llm") {
      text = o.error || o.reply || "";
    } else if (o.kind === "write_file") {
      text = o.error || `wrote ${o.bytes} bytes → ${o.path}`;
    } else if (o.kind === "edit_file") {
      text = o.error || `replaced ${o.replacements} occurrence(s) in ${o.path}`;
    } else if (o.kind === "memory_read") {
      text = o.missing ? `no note "${o.name}" (topics: ${(o.topics || []).join(", ") || "none yet"})`
        : o.content || "";
    } else if (o.kind === "memory_write") {
      text = o.deleted ? `note "${o.name}.md" ${o.existed ? "deleted, INDEX updated" : "did not exist"}`
        : `note "${o.name}.md" ${o.created ? "created" : "revised"} (${o.lines} lines), INDEX updated`;
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
      text = o.dialog ? `dialog reply (question stays open): ${o.user_message}`
        : o.answered ? `answered: ${o.answer}`
        : o.timed_out ? "timed out → deferred" : "filed as deferred";
    } else if (o.kind === "finish" && o.rejected) {
      text = "finish REJECTED — no action had been executed yet (fabrication guard)";
    } else {
      text = JSON.stringify(o, null, 1);
    }
    const obs = obsBody(o.kind, text, (o.kind === "llm" && !o.error)
      || (o.kind === "memory_read" && !o.missing));
    if (openTurn) { openTurn.append(obs); openTurn = null; }
    else root.append(el("div", { class: "turn" }, obs));
  }

  const SIMPLE = {
    user_injection: (ev) => ev.payload.source === "engine"
      ? el("div", { class: "ev system" }, `— ${ev.payload.text} —`)
      : el("div", { class: "ev injection" }, `\u{1F4E8} user: ${ev.payload.text}`),
    question: questionNode,
    answer: (ev) => {
      if (!ev.payload.intermediate) closeQuestion(ev.payload.qid);   // dialog replies keep it open
      return el("div", { class: "ev answer" }, ev.payload.intermediate
        ? `💬 reply (${ev.payload.source}, dialog): ${ev.payload.text}`
        : `✅ answer (${ev.payload.source}): ${ev.payload.text}`);
    },
    error: (ev) => el("div", { class: "ev error" },
      `error (${ev.payload.where}${ev.payload.attempt ? `, attempt ${ev.payload.attempt}` : ""}): ${ev.payload.message}`),
    compaction: (ev) => el("div", { class: "ev compaction" },
      `— context compacted: ${ev.payload.before_chars} → ${ev.payload.after_chars} chars —`),
    subrun_start: (ev) => el("div", { class: "ev subrun" }, subrunNode(ev,
      `${ev.payload.mode === "sequential" ? "→ subtask" : "↳ subrun"} ${ev.payload.n} "${ev.payload.label}" started (${ev.payload.workflow}, depth ${ev.payload.depth})`)),
    subrun_end: (ev) => el("div", { class: "ev subrun" }, subrunNode(ev,
      `${ev.payload.mode === "sequential" ? "→ subtask" : "↰ subrun"} ${ev.payload.n} "${ev.payload.label}" ${ev.payload.status} — ${ev.payload.turns} turns, ${fmtTokens(ev.payload.usage)}`,
      md(ev.payload.summary || "(no summary)", "obs md"))),
    header: (ev) => el("div", { class: "ev system" },
      `run ${ev.run_id} · ${ev.orchestrator?.endpoint}:${ev.orchestrator?.model} · workflow ${ev.workflow?.slug || "?"}`),
  };

  return {
    // Close inline forms whose question is no longer open anywhere (answered elsewhere or
    // consumed by a later run). Fresh forms are spared: `open` may predate them.
    reconcileQuestions(open, fetchedAt = Date.now()) {
      for (const [qid, f] of [...qforms]) {
        if (open.has(qid) || fetchedAt - f.created < 3000) continue;
        closeQuestion(qid, "✓ settled (answered on the Decisions page or in a later run)");
      }
    },
    closeQuestion,
    add(ev) {
      if (ev.type === "assistant_action") return void addTurn(ev);
      if (ev.type === "observation") return void addObservation(ev);
      if (ev.type === "finish") {
        const p = ev.payload;
        root.append(el("div", { class: `finish-banner ${p.status}` },
          el("strong", {}, `finish: ${p.status}`),
          el("div", { class: "mt", style: "margin-top:6px" }, md(p.summary || "")),
          el("div", { class: "muted", style: "margin-top:6px" },
            `${ev.turns ?? "?"} turns · ${fmtTokens(ev.usage_total)}`)));
        return;
      }
      const renderer = SIMPLE[ev.type];
      if (renderer) root.append(renderer(ev));
    },
  };
}
