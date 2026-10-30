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
//   slug               — the ROUTINE whose transcript this is; makes a reminder id in a side
//                        field a link to its row in that routine's Recipe health (F591).
//                        Omitted = the id renders as the plain code it names, because a link
//                        with no routine to point at is worse than dead text.
//   blobs              — an object-URL array to register thumbnails in instead of owning one
//                        (a nested subrun transcript shares its parent's, so one destroy()
//                        frees the tree). Omitted = this transcript owns its own.

import { actionTime } from "/static/components/actiontime.js";
import { md, mdInline } from "/static/md.js";
import { answerForm } from "/static/components/answerform.js";
import { highlightJson } from "/static/components/code.js";
import { ruleFireStrip, sideFieldRows } from "/static/components/concept-rows.js";
import { ruleLink, utilLink } from "/static/components/conceptlinks.js";
import { accountingLine } from "/static/components/obs-accounting.js";
import { attachmentRow, revokeBlobs } from "/static/components/obs-attachments.js";
// …and re-exported, because this file is still their public entry: components/chat.js imports
// both from here, and that file is AT its own ratchet ceiling with zero slack, so it cannot
// afford the second import line a direct dependency would cost it. A re-export is also the
// honest shape — nothing about an attachment row is the transcript's, and a later change may
// repoint chat.js at the module itself without touching this one.
export { attachmentRow, revokeBlobs };
import { killBrief } from "/static/components/obs-background.js";
import { OVERSIGHT_TEXT, actionCancelledText, compactionText, headerText, stagesSkippedText }
  from "/static/components/obs-records.js";
import { obsState, observationText } from "/static/components/obs-text.js";
import { turnUsage } from "/static/components/turn-model.js";
import { el, fmtTime, fmtTokens } from "/static/util.js";

// WHAT THIS FILE OWNS, and what it does not. The transcript is the console's largest renderer
// and it is over the house bar, so its vocabulary lives in modules beside it and this file
// keeps what needs the live PAGE — the turn boxes, the collapsing folds, the inline answer
// forms, the subrun unfolding, the clocks:
//   obs-text.js         what an observation SAYS (the per-kind chain) and what it MEANS (the
//                       row's state word) — pure payload-in, string-out
//   obs-attachments.js  the attachment thumbnails and the object URLs they hold open
//   obs-background.js   a backgrounded call's four endings, and the kill confirmation
//   obs-accounting.js   the finish accounting's two renderers
//   concept-rows.js     the side-field rows + the RULE-fired strip, each linking what it names
//   turn-model.js       which model served a turn, and what it cost
// Two things stay HERE because a source-level guard reads them from this file by name:
// `BRIEF_FIELD` (tests/test_actions.py holds it in lockstep with engine/actionschema.py) and
// the `SIMPLE` renderer table (tests/test_static_transcript.py proves every EVENT_TYPES member
// has a renderer in it).

// Mirror of engine/actionschema.py BRIEF_FIELD (the source of truth; static/ is no-build and
// cannot import it) — a kind missing here renders its turn line with an EMPTY brief, which is
// how this map drifted 10 kinds behind before the 2026-08-21 sweep caught it.
// tests/test_actions.py now holds the two in lockstep.
const BRIEF_FIELD = { util: "name", write_util: "name", remove_util: "name",
                      read_file: "path", view_image: "path", write_file: "path",
                      edit_file: "path", delete: "path", move: "src", mkdir: "path",
                      memory_read: "name", memory_write: "name",
                      read_rule: "name", write_rule: "name", script: "name",
                      shell: "command",
                      llm: "prompt", decide: "question", spawn: "label", subtask: "label",
                      detach: "label",
                      schedule_run: "target", create_routine: "target",
                      manage_lane: "verb", job: "id", goal: "id", kill: "n", wait: "n",
                      ask_user: "question", report: "title", finish: "status" };

// "Refer to" rides the message TEXT as one leading quoted line — `> re <label>: <snippet>`,
// then a blank line, then the message. Plain markdown the model reads naturally, no new
// event field; this is the ONE convention (composers prepend it, renderers split it).
const REF_LINE = /^> re ([^\n]+)\n\n?/;
export function splitRef(text) {
  const m = REF_LINE.exec(text || "");
  return m ? { ref: m[1], body: (text || "").slice(m[0].length) } : { ref: null, body: text || "" };
}

// The operator's ⚑ on a conversation reply (engine/refusal.record_operator_flag), in ONE
// sentence for the chat's line and the run view's transcript alike.
export function operatorFlagText(p) {
  return `⚑ you flagged the reply at turn ${p.turn ?? "?"}${p.model ? ` (${p.model})` : ""} `
    + "as a refusal — it and everything after it were archived and your message was re-sent; "
    + `${p.takeover_model || "the uncensored model"} carries the conversation from here`;
}

export function referButton(onRefer, label, snippet) {
  if (!onRefer) return null;
  return el("button", { class: "refer-btn", title: "refer to this in your next message",
    onclick: () => onRefer({ label,
      snippet: String(snippet || "").replace(/\s+/g, " ").trim().slice(0, 160) }) }, "↩");
}


export function createTranscript(container, opts = {}) {
  const root = el("div", { class: "transcript" });
  container.append(root);
  let openTurn = null; // the turn box awaiting its observation
  let openClock = null; // timing feedback for the action awaiting completion
  let lastPhase = null; // the acting stage of the previous turn — a change inserts a divider
  const qforms = new Map();   // qid -> { controls, created } for open inline answer forms
  // Object URLs the attachment thumbnails below hold open (api.js hands the caller the
  // lifetime). A nested subrun transcript is handed THIS array, so the one destroy() at the
  // view's teardown frees the whole tree — and a conversation left open all day stops
  // accumulating a decoded image per attachment per re-render.
  const blobs = opts.blobs ?? [];
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
    // Same shape as a user message below: label as its own line, body as a block. The
    // question is the one event in the stream a person has to act on, so a table or a list it
    // lays out has to render — and the options and the default read as their own lines rather
    // than as a tail on a paragraph.
    const head = el("div", {}, el("div", { class: "evlabel" }, `❓ [${label}]`),
      md(p.question || "", "md prose"),
      p.options?.length
        ? el("div", { class: "faint small" }, `options: ${p.options.join(" | ")}`) : null,
      p.default
        ? el("div", { class: "faint small" }, `without an answer: ${p.default}`) : null);
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
        fileUrl: opts.fileUrl, blobs, slug: opts.slug });   // a child's reminders are the same routine's
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
    openClock?.stop(ev.ts, false);
    openClock = null;
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
      // A kill carrying a background HANDLE: `BRIEF_FIELD.kill` is `n` and stays so (the map is
      // held in lockstep with engine/actionschema.py by tests/test_actions.py), so the brief
      // would be blank for the whole handle namespace — obs-background.js names it instead.
      : killBrief(a)
      ?? String(a[BRIEF_FIELD[a.kind]] ?? "").slice(0, 200);
    const turn = el("div", { class: "turn", "data-turn": ev.turn ?? null },   // D193: the rail links here
      el("div", { class: "say" },
        // turn count + timestamp stack vertically (.turnmeta) so the timestamp sits UNDER
        // the turn count rather than beside it — reclaims horizontal space for the say text.
        el("div", { class: "turnmeta" },
          el("span", { class: "n" }, `turn ${ev.turn ?? "?"}${ev.chained ? ` · ${ev.chained}` : ""}`),   // `· n` = D203's chained action, same turn
          ev.ts ? el("span", { class: "ts", title: ev.ts }, fmtTime(ev.ts)) : null),
        el("span", { class: "saytext" }, mdInline(a.say || ""))),
      // The SIDE FIELDS — `note`, `remind`, `remind_feedback` — ride ANY action and cost the
      // same nothing, so they render as rows beside the say rather than hiding in the json
      // fold. components/concept-rows.js words them and links the reminder each one NAMES,
      // which needs the routine whose state that reminder is: `opts.slug` (F591).
      sideFieldRows(a, opts.slug),
      el("div", { class: "act" },
        el("span", {}, a.kind),
        // A util's NAME is a concept with a page of its own, so it links there (operator,
        // 2026-09-30). Only the name is linked, never the args beside it, and only from the
        // structured `a.name` field — a util called `job` or `git` would match ordinary
        // English everywhere if this were done by scanning prose (see conceptlinks.js).
        a.kind === "util" && a.name
          ? el("span", { class: "muted" }, utilLink(a.name),
              Array.isArray(a.args) && a.args.length
                ? " " + a.args.join(" ").slice(0, 200) : "  (no args)")
          // `read_rule` / `write_rule` carry a rule SLUG in the same field, and "list" is the
          // catalog rather than a rule, so it stays plain.
          : (a.kind === "read_rule" || a.kind === "write_rule") && a.name && a.name !== "list"
          ? el("span", { class: "muted" }, ruleLink(a.name))
          : el("span", { class: "muted" }, brief),
        turnUsage(ev)),    // D188: which model served this turn, then what it cost
      // Syntax-highlighted, because this fold is where a reader goes to reproduce a call and
      // an unlit blob of 40 lines hides the one field they came for (operator, 2026-09-30:
      // "i want the action JSON in a message element to be syntax highlighted"). Display only
      // — highlightJson builds spans via textContent and preserves the source exactly, so the
      // fold still copies back as valid JSON.
      el("details", { class: "raw" }, el("summary", {}, "action json"),
        el("pre", { class: "hl-json" }, highlightJson(JSON.stringify(a, null, 1)))),
      referBtn(`turn ${ev.turn ?? "?"} (${a.kind}${a.kind === "util" && a.name ? ` ${a.name}` : ""})`,
        a.say || brief));
    // F586/D160-C: the × that stops a running util/script/shell call. The view supplies the
    // POST (opts.cancelAction); actionTime decides when the control may show itself, because it
    // is what knows the row is still running and the view is live.
    const clock = actionTime(ev, opts.isLive, opts.cancelAction);
    turn.append(clock.node);
    root.append(turn);
    openClock = clock;
    openTurn = a.kind === "finish" ? null : turn;
    return turn;
  }

  // Tool/observation return values are collapsed by default (expandable). The summary carries the
  // first line, so short one-line results stay fully readable without expanding. `rich` renders
  // the body as simple markdown (model-authored prose — llm replies); program output stays literal.

  function obsBody(text, rich = false, state = "") {
    const firstLine = (text.split("\n")[0] || "").slice(0, 120);
    const more = text.length > firstLine.length;
    return el("details", { class: `obs-collapse${state ? ` obs-${state}` : ""}` },
      el("summary", {}, `result — ${firstLine}${more ? " …" : ""}`),
      rich ? md(text, "obs md") : el("div", { class: "obs" }, text));
  }

  function addObservation(ev) {
    const o = ev.payload;
    // The WORDS and the row's state word come from components/obs-text.js, which owns the
    // per-kind vocabulary; what is left here is the page — the collapsing fold, the clocks,
    // and a `view_image`'s real thumbnails, which need the authenticated blob route an
    // <img src> cannot carry an Authorization header for (F559).
    const text = observationText(o);
    const thumbs = o.kind === "view_image"
      ? attachmentRow((o.files || []).filter((f) => !f.error).map((f) => f.path),
                      opts.fileUrl, blobs)
      : null;
    const obs = obsBody(text, (o.kind === "llm" && !o.error)
      || (o.kind === "memory_read" && !o.missing), obsState(o));
    if (thumbs) obs.append(thumbs);
    // A rule FIRE is not an ordinary result: its strip rides the turn OUTSIDE the collapsed
    // fold, so a reader sees that a rule governed this moment without expanding anything
    // (F591; components/concept-rows.js words it and links the rule from `o.assists`).
    const fire = ruleFireStrip(o);
    openClock?.stop(ev.ts);
    openClock = null;
    if (openTurn) { openTurn.append(...[fire, obs].filter(Boolean)); openTurn = null; }
    else root.append(el("div", { class: "turn" }, fire, obs));
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
      // An escalation ladder's DIRECTIVE arrives on this same event (engine/ladder.py files it
      // through the `oversight` channel), and it is not the operator speaking: it is a rung
      // above redirecting the run, and the reader has to be able to tell who did. The channel
      // in the payload is what says so — labelled by its rung, with its own class.
      const rung = ev.payload.via === "oversight";
      return el("div", { class: `ev injection${rung ? " oversight" : ""}`,
                         ...(rung ? { "data-oversight-directive": "" } : {}) },
        ref ? el("div", { class: "reply-ref", title: ref }, "↩ ", ref) : null,
        evlabel(rung ? `\u2191 oversight directive (${ev.payload.source || "a rung above"}): `
                     : "\u{1F4E8} user: "), md(body),
        attachmentRow(ev.payload.attachments, opts.fileUrl, blobs));
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
    // The refusal record (engine/refusal.py): the flag, the takeover verdict (with the
    // back-off episode's level and window), and the takeover model's reply head when it
    // served. The OPERATOR seam is the person's own — a reply flagged by hand, whose
    // takeover model then answers for good.
    refusal: (ev) => {
      const p = ev.payload;
      if (p.where === "operator") {
        return el("div", { class: "ev refusal operator-flag" }, operatorFlagText(p));
      }
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
    compaction: (ev) => el("div", { class: "ev compaction" },
      compactionText(ev.payload)),
    subrun_start: (ev) => el("div", { class: "ev subrun" }, subrunNode(ev,
      `${ev.payload.mode === "sequential" ? "→ subtask" : "↳ subrun"} ${ev.payload.n} "${ev.payload.label}" started (${ev.payload.workflow}, depth ${ev.payload.depth})`)),
    subrun_end: (ev) => el("div", { class: "ev subrun" }, subrunNode(ev,
      `${ev.payload.mode === "sequential" ? "→ subtask" : "↰ subrun"} ${ev.payload.n} "${ev.payload.label}" ${ev.payload.status} — ${ev.payload.turns} turns, ${fmtTokens(ev.payload.usage)}`,
      md(ev.payload.summary || "(no summary)", "obs md"))),
    // The run's accounting, stamped where every reader finds it at the finish — or the finish
    // line reached. A neutral line, like compaction: it records a state change the reader
    // needs to see in place, not an action the model took.
    stopping_update: (ev) => el("div", { class: "ev compaction", "data-accounting-event": "" },
      accountingLine(ev.payload || {})),
    // The neutral RECORDS below are worded by components/obs-records.js; what stays here is
    // each row's class and data-attribute, which the stylesheet and the browser suite key off.
    // The PALETTE is the decision this table keeps: `oversight_failed` draws in the error
    // palette because the mechanism itself broke, while `oversight_skipped` is the ordinary
    // ceiling case, and `action_cancelled` draws in the injection palette because somebody
    // outside the run reached in and nothing failed.
    stages_skipped: (ev) => el("div", { class: "ev compaction", "data-stages-skipped": "" },
      stagesSkippedText(ev.payload)),
    oversight_dispatch: (ev) => el("div", { class: "ev oversight", "data-oversight-dispatch": "" },
      OVERSIGHT_TEXT.dispatch(ev.payload)),
    oversight_directive: (ev) => el("div", { class: "ev oversight", "data-oversight-verdict": "" },
      OVERSIGHT_TEXT.directive(ev.payload)),
    oversight_skipped: (ev) => el("div", { class: "ev compaction", "data-oversight-skipped": "" },
      OVERSIGHT_TEXT.skipped(ev.payload)),
    oversight_failed: (ev) => el("div", { class: "ev error", "data-oversight-failed": "" },
      OVERSIGHT_TEXT.failed(ev.payload)),
    oversight_no_directive: (ev) => el("div", { class: "ev compaction", "data-oversight-none": "" },
      OVERSIGHT_TEXT.none(ev.payload)),
    action_cancelled: (ev) => el("div", { class: "ev cancelled", "data-action-cancelled": "" },
      actionCancelledText(ev.payload)),
    header: (ev) => el("div", { class: "ev system" }, headerText(ev)),
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
    /** The view's teardown: free the attachment thumbnails' object URLs. */
    destroy() { revokeBlobs(blobs); },
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
        openClock?.stop(ev.ts);
        openClock = null;
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
