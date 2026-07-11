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

import { el, fmtTime, fmtTokens, toast } from "/static/util.js";

const BRIEF_FIELD = { util: "name", write_util: "name", read_file: "path", write_file: "path",
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
    const head = `❓ [${p.mode}] ${p.question}` +
      (p.options?.length ? ` — options: ${p.options.join(" | ")}` : "");
    // Inline answering: deferred questions used to be dead text here, answerable only on
    // the Decisions page. Blocking ones stay with the run view's panel (it handles dialog).
    if (!opts.answer || !p.qid || p.mode !== "deferred") return el("div", { class: "ev question" }, head);
    const input = el("input", { type: "text", placeholder: "answer here — or on the Decisions page…",
                                style: "flex:1" });
    const send = el("button", { class: "btn small primary" }, "answer");
    const controls = el("div", { class: "row mt", style: "gap:6px" },
      p.options?.length ? p.options.map((o) =>
        el("button", { class: "btn small", onclick: () => { input.value = o; input.focus(); } }, o)) : null,
      input, send);
    const submit = async () => {
      if (!input.value.trim()) return;
      send.disabled = true;
      try {
        await opts.answer(p.qid, input.value.trim());
        closeQuestion(p.qid, `✅ answered: ${input.value.trim()} (queued for the next run)`);
      } catch (err) { send.disabled = false; toast(err.message, 4000, { error: true }); }
    };
    send.onclick = submit;
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } };
    qforms.set(p.qid, { controls, created: Date.now() });
    return el("div", { class: "ev question" }, el("div", {}, head), controls);
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
      : String(a[BRIEF_FIELD[a.kind]] ?? "").slice(0, 200);
    const turn = el("div", { class: "turn" },
      el("div", { class: "say" },
        el("span", { class: "n" }, `turn ${ev.turn ?? "?"}`),
        ev.ts ? el("span", { class: "ts", title: ev.ts }, fmtTime(ev.ts)) : null,
        el("span", { class: "saytext" }, a.say || "")),
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
      text = o.dialog ? `dialog reply (question stays open): ${o.user_message}`
        : o.answered ? `answered: ${o.answer}`
        : o.timed_out ? "timed out → deferred" : "filed as deferred";
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
      `↳ subrun ${ev.payload.n} "${ev.payload.label}" started (${ev.payload.workflow}, depth ${ev.payload.depth})`)),
    subrun_end: (ev) => el("div", { class: "ev subrun" }, subrunNode(ev,
      `↰ subrun ${ev.payload.n} "${ev.payload.label}" ${ev.payload.status} — ${ev.payload.turns} turns, ${fmtTokens(ev.payload.usage)}`,
      el("div", { class: "obs" }, ev.payload.summary || "(no summary)"))),
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
