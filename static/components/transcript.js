// Shared transcript event renderer — one function per event type, keyed off the
// transcript JSONL contract (assistant_action/observation pairs render as one turn box).
//
// opts wire the conversation to the live system (all optional — omitted = plain rendering):
//   answer(qid, text, decision) — enables inline answering on question events: a full form
//                        for DEFERRED questions, a one-click button strip for BLOCKING
//                        options/access-request ones (the pinned panel keeps the one full
//                        form — F264; the strip keeps the decision in view at the tail — R132).
//   loadSub(n, offset) — enables expanding a subrun's own conversation in place under its
//                        start/end lines; n may be a nested path like "2/1". Returns
//                        {events, offset}.
//   isLive()           — true while the run is live; expanded subruns keep polling.
//   onRefer({label, snippet}) — enables a hover "refer to" button on every message (the
//                        messenger reply analog); the view primes its composer with it.
//   fileUrl(rel)       — maps a message attachment's rel path (e.g. "attachments/x.png") to
//                        this mount's serving route, enabling inline thumbnails on injected
//                        user messages; omitted = the text block's plain list stands alone.

import { apiBlobUrl } from "/static/api.js";
import { md, mdInline } from "/static/md.js";
import { answerForm } from "/static/components/answerform.js";
import { el, fmtTime, fmtTokens, fullOutput } from "/static/util.js";

// Mirror of engine/actions.py BRIEF_FIELD (the source of truth) — a kind missing here
// renders its turn line with an EMPTY brief, which is how this map drifted 10 kinds
// behind before the 2026-08-21 sweep caught it. Keep the two in lockstep.
const BRIEF_FIELD = { util: "name", write_util: "name", remove_util: "name",
                      read_file: "path", view_image: "path", write_file: "path",
                      edit_file: "path", memory_read: "name", memory_write: "name",
                      read_rule: "name", write_rule: "name", script: "name",
                      llm: "prompt", spawn: "label", subtask: "label", detach: "label",
                      schedule_run: "target", create_routine: "target",
                      manage_group: "verb", kill: "n", wait: "n",
                      ask_user: "question", report: "title", finish: "status" };

// "Refer to" rides the message TEXT as one leading quoted line — `> re <label>: <snippet>`,
// then a blank line, then the message. Plain markdown the model reads naturally, no new
// event field; this is the ONE convention (composers prepend it, renderers split it).
const REF_LINE = /^> re ([^\n]+)\n\n?/;
export function splitRef(text) {
  const m = REF_LINE.exec(text || "");
  return m ? { ref: m[1], body: (text || "").slice(m[0].length) } : { ref: null, body: text || "" };
}

export function referButton(onRefer, label, snippet) {
  if (!onRefer) return null;
  return el("button", { class: "refer-btn", title: "refer to this in your next message",
    onclick: () => onRefer({ label,
      snippet: String(snippet || "").replace(/\s+/g, " ").trim().slice(0, 160) }) }, "↩");
}

// Inline rendering for a user message's file attachments (user_injection
// payload.attachments): images become thumbnails loaded through the authenticated
// blob route (a bare <img src> cannot carry the Authorization header — the artifact
// panel's pattern), everything else a fetch-and-open chip. Shared by the transcript's
// injection renderer and the conversation chat's user bubbles.
const ATT_IMG = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"]);

export function attachmentRow(rels, fileUrl) {
  if (!rels?.length || !fileUrl) return null;
  const row = el("div", { class: "att-row" });
  for (const rel of rels) {
    const name = String(rel).split("/").pop();
    const ext = (name.split(".").pop() || "").toLowerCase();
    if (ATT_IMG.has(ext)) {
      const img = el("img", { class: "att-thumb", alt: name, title: `${name} — click to open` });
      img.onclick = () => { if (img.src) window.open(img.src, "_blank"); };
      apiBlobUrl(fileUrl(rel)).then(({ url }) => { img.src = url; })
        .catch(() => img.replaceWith(
          el("span", { class: "faint small" }, `🖼 ${name} (unavailable)`)));
      row.append(img);
    } else {
      const btn = el("button", { class: "btn small att-file", title: rel }, `📎 ${name}`);
      btn.onclick = () => apiBlobUrl(fileUrl(rel))
        .then(({ url }) => window.open(url, "_blank"))
        .catch((err) => window.alert(`could not load ${name}: ${err.message}`));
      row.append(btn);
    }
  }
  return row;
}

export function createTranscript(container, opts = {}) {
  const root = el("div", { class: "transcript" });
  container.append(root);
  let openTurn = null; // the turn box awaiting its observation
  let lastPhase = null; // the acting stage of the previous turn — a change inserts a divider
  const qforms = new Map();   // qid -> { controls, created } for open inline answer forms
  const referBtn = (label, snippet) => referButton(opts.onRefer, label, snippet);

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
    if (!opts.answer || !p.qid) return el("div", { class: "ev question" }, head);
    if (p.mode !== "deferred") {
      // BLOCKING: the run view's pinned panel owns the ONE full form (free text, ask-back,
      // dialog — F264). The transcript adds the one-click strip because the panel sits at
      // the page TOP while a followed tail reads at the bottom — a util approval scrolled
      // past with no buttons in sight sent the user hunting through the Decisions tab
      // (R132). Same qforms registry, so a panel/Decisions answer settles this strip too.
      if (!(p.options?.length || (Array.isArray(p.request) && p.request.length))) {
        return el("div", { class: "ev question" }, head);
      }
      const strip = answerForm(p, {
        quick: true,
        defaultLine: false,   // the head already states the default inline
        submitText: (text, _intermediate, decision) => opts.answer(p.qid, text, decision),
        onSuccess: (text) => closeQuestion(p.qid, `✅ answered: ${text}`),
      });
      qforms.set(p.qid, { controls: strip.node, created: Date.now() });
      return el("div", { class: "ev question" }, el("div", {}, head), strip.node);
    }
    const form = answerForm(p, {
      placeholder: "answer here — or on the Decisions page… (Shift+Enter sends)",
      defaultLine: false,   // the head already states the default inline
      submitText: (text, _intermediate, decision) => opts.answer(p.qid, text, decision),
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
        loadSub: (m, o) => opts.loadSub(`${p.n}/${m}`, o), isLive: opts.isLive,
        fileUrl: opts.fileUrl });
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
    // Group the flat stream by acting stage: assistant_action events carry the live phase
    // (stamped from stage-module reads) — a change draws a labeled divider, so the say
    // stream reads as a story chaptered by the routine's own stages.
    if (ev.phase && ev.phase !== lastPhase) {
      lastPhase = ev.phase;
      root.append(el("div", { class: "phase-divider" }, el("span", {}, ev.phase)));
    }
    // For utils, show the whole call inline (name + args) — a missing args array must be
    // visible at a glance, not one click deep in the action json.
    const brief = a.kind === "util"
      ? `${a.name ?? ""}${Array.isArray(a.args) && a.args.length ? " " + a.args.join(" ") : "  (no args)"}`.slice(0, 200)
      : a.kind === "read_file" && Array.isArray(a.paths)
      ? a.paths.join(", ").slice(0, 200)
      : String(a[BRIEF_FIELD[a.kind]] ?? "").slice(0, 200);
    const turn = el("div", { class: "turn" },
      el("div", { class: "say" },
        // turn count + timestamp stack vertically (.turnmeta) so the timestamp sits UNDER
        // the turn count rather than beside it — reclaims horizontal space for the say text.
        el("div", { class: "turnmeta" },
          el("span", { class: "n" }, `turn ${ev.turn ?? "?"}`),
          ev.ts ? el("span", { class: "ts", title: ev.ts }, fmtTime(ev.ts)) : null),
        el("span", { class: "saytext" }, mdInline(a.say || ""))),
      a.note ? el("div", { class: "note", title: "captured to state/notes.md" },
        "📌 ", mdInline(a.note)) : null,
      el("div", { class: "act" },
        el("span", {}, a.kind),
        el("span", { class: "muted" }, brief),
        ev.usage ? el("span", { class: "muted", style: "margin-left:auto",
                              title: ev.usage.provider ? `served by ${ev.usage.provider}` : "" },
                    fmtTokens(ev.usage)) : null),
      el("details", { class: "raw" }, el("summary", {}, "action json"),
        el("pre", {}, JSON.stringify(a, null, 1))),
      referBtn(`turn ${ev.turn ?? "?"} (${a.kind}${a.kind === "util" && a.name ? ` ${a.name}` : ""})`,
        a.say || brief));
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
          + fullOutput(o.full_output)
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

  // Human-authored message bodies (injections, answers) are prose exactly like the model's
  // own — a pasted list, fence or link must render, not sit there as literal asterisks. So
  // they go through the same md() pipeline as summaries: label as its own line, body as a
  // block (md() is a superset of mdInline(), so nothing reads worse than it did).
  const evlabel = (text) => el("div", { class: "evlabel" }, text);

  const SIMPLE = {
    user_injection: (ev) => {
      if (ev.payload.source === "engine") {
        return el("div", { class: "ev system" }, `— ${ev.payload.text} —`);
      }
      const { ref, body } = splitRef(ev.payload.text);
      return el("div", { class: "ev injection" },
        ref ? el("div", { class: "reply-ref", title: ref }, "↩ ", ref) : null,
        evlabel("\u{1F4E8} user: "), md(body),
        attachmentRow(ev.payload.attachments, opts.fileUrl));
    },
    question: questionNode,
    answer: (ev) => {
      if (!ev.payload.intermediate) closeQuestion(ev.payload.qid);   // dialog replies keep it open
      const p = ev.payload;
      return el("div", { class: "ev answer" },
        evlabel(p.intermediate ? `💬 reply (${p.source}, dialog): ` : `✅ answer (${p.source}): `),
        md(p.text || ""));
    },
    // A schema/transport error card shows WHY the attempt was rejected; the raw reply the
    // model actually sent (persisted by the engine as payload.raw, capped 1500 chars) folds
    // underneath — without it the reader sees the rejection but never what was tried.
    error: (ev) => el("div", { class: "ev error" },
      el("div", {},
        `error (${ev.payload.where}${ev.payload.attempt ? `, attempt ${ev.payload.attempt}` : ""}` +
        `${ev.payload.provider ? `, via ${ev.payload.provider}` : ""}): ${ev.payload.message}`),
      ev.payload.raw ? el("details", { class: "raw" },
        el("summary", {}, "attempted reply"),
        el("pre", {}, ev.payload.raw)) : null),
    // The refusal-clarification record (engine/refusal.py): the flag, the isolated
    // trigger fragment, and the harness's pretend-compliance — shown as evidence, never
    // as an answer (the uncensored role is a honeypot harness by design).
    refusal: (ev) => {
      const p = ev.payload;
      return el("div", { class: "ev refusal" },
        el("div", {},
          `⛔ refusal flagged (${p.where}${p.model ? ` · ${p.model}` : ""}): ${p.message || ""}`),
        p.isolated ? el("div", { class: "faint small" },
          `isolated ${p.isolated_kind || "fragment"}: “${p.isolated}”`
          + (p.referred ? ` → fragment referred to the ${p.harness_model || "uncensored"} harness`
                        : " — not referred")) : null,
        p.isolation_error ? el("div", { class: "faint small" },
          `isolation failed: ${p.isolation_error} — nothing referred`) : null,
        p.harness_reply ? el("details", { class: "raw" },
          el("summary", {}, "harness reply (diagnostic — not an answer)"),
          el("pre", {}, p.harness_reply)) : null);
    },
    compaction: (ev) => {
      // Three payload shapes, one line each (never "undefined → undefined"): the LLM
      // archive / deterministic digest (flat before/after), the hard window clamp
      // ({clamp}), and the provider-window correction ({window_guard}, optional clamp).
      const p = ev.payload || {};
      const c = p.clamp || p.window_guard?.clamp || null;
      const span = (o) => `${o.before_chars} → ${o.after_chars} chars`;
      let text;
      if (p.window_guard) {
        const g = p.window_guard;
        text = `— window corrected: ${g.model} really holds ${g.corrected_chars} chars` +
               `${c ? `; clamped ${c.clamped_messages} oversized, ${span(c)}` : ""} —`;
      } else if (c) {
        text = `— window clamp: ${c.clamped_messages} oversized ` +
               `${c.clamped_messages === 1 ? "body" : "bodies"} trimmed in place, ${span(c)} —`;
      } else if (p.mode === "llm-history") {
        text = `— context archived: ${p.elided_messages} messages → history/ ` +
               `(${p.history_files} files, browsable in the rail's files card), ${span(p)} —`;
      } else if (p.before_chars != null) {
        text = `— context compacted: ` +
               `${p.elided_messages ? `${p.elided_messages} messages digested, ` : ""}${span(p)} —`;
      } else {
        // archival failed AND the digest had nothing to elide — reason-only record
        text = `— compaction: nothing elided this pass —`;
      }
      if (p.archival_degraded) {
        // a failed LLM archival is a designed degrade (the digest took the pass), not a
        // run error (F376) — note it on the neutral line instead of a red error card
        text += ` (history archival degraded: ${p.archival_degraded})`;
      }
      return el("div", { class: "ev compaction" }, text);
    },
    subrun_start: (ev) => el("div", { class: "ev subrun" }, subrunNode(ev,
      `${ev.payload.mode === "sequential" ? "→ subtask" : "↳ subrun"} ${ev.payload.n} "${ev.payload.label}" started (${ev.payload.workflow}, depth ${ev.payload.depth})`)),
    subrun_end: (ev) => el("div", { class: "ev subrun" }, subrunNode(ev,
      `${ev.payload.mode === "sequential" ? "→ subtask" : "↰ subrun"} ${ev.payload.n} "${ev.payload.label}" ${ev.payload.status} — ${ev.payload.turns} turns, ${fmtTokens(ev.payload.usage)}`,
      md(ev.payload.summary || "(no summary)", "obs md"))),
    header: (ev) => el("div", { class: "ev system" },
      `run ${ev.run_id} · ${ev.orchestrator?.endpoint}:${ev.orchestrator?.model} · workflow ${ev.workflow?.slug || "?"}`),
  };

  // What a "refer to" on a simple event means to the model reading the quote later —
  // labels are addressed to it ("your question"), snippets are the message's own words.
  const REFER_SIMPLE = {
    user_injection: (ev) => ev.payload.source === "engine" ? null
      : ["the earlier user message", splitRef(ev.payload.text).body],
    question: (ev) => ["your question", ev.payload.question],
    answer: (ev) => ["the user's answer", ev.payload.text],
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
            `${ev.turns ?? "?"} turns · ${fmtTokens(ev.usage_total)}`),
          referBtn(`your ${p.status} finish summary`, (p.summary || "").split("\n")[0])));
        return;
      }
      const renderer = SIMPLE[ev.type];
      if (renderer) {
        const node = renderer(ev);
        const ref = REFER_SIMPLE[ev.type]?.(ev);
        const btn = ref && referBtn(ref[0], ref[1]);
        if (btn) node.append(btn);
        root.append(node);
      }
    },
  };
}
