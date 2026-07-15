// Chat-first transcript renderer for conversations: user messages and the model's replies
// (finish summaries) are the conversation; the tool work between them folds into one
// expandable group per reply, rendered at full altitude by the shared transcript component.
//
// opts:
//   answer(qid, text) — inline answering for deferred questions (same contract as transcript.js)
//   loadSub(n, off)   — subrun expansion inside the work fold
//   isLive()          — live-run predicate for subrun polling
//   onArtifact(path)  — a write_file into artifacts/ landed (the panel refreshes)
//   onFork(title, qt) — the user clicked the [new-topic] fork button

import { createTranscript } from "/static/components/transcript.js";
import { answerForm } from "/static/components/answerform.js";
import { md, mdInline } from "/static/md.js";
import { el, fmtTime, fmtTokens } from "/static/util.js";

const NEW_TOPIC = /^\s*\[new-topic\]\s*(.*)$/;
const ATTACH_BLOCK = /\n?\n?\[attached files[^\]]*\]\n((?:- .*\n?)+)/;

// A user message may carry the attachment block the API appended — render it as chips.
function userNode(text) {
  const m = ATTACH_BLOCK.exec(text);
  const body = m ? text.replace(ATTACH_BLOCK, "").trimEnd() : text;
  const chips = m ? m[1].trim().split("\n").map((line) => {
    const p = line.replace(/^- /, "").trim();
    return el("span", { class: "attach-chip", title: p }, "📎 ", p.split("/").pop());
  }) : [];
  return el("div", { class: "msg user" },
    el("div", { class: "msg-body" }, md(body)),
    chips.length ? el("div", { class: "msg-attach" }, chips) : null);
}

export function createChat(container, opts = {}) {
  const root = el("div", { class: "chat" });
  container.append(root);

  let fold = null;         // { details, summary, transcript, steps } — the open work group
  let lastUser = "";       // the newest user message — the fork button pre-fills with it

  function ensureFold() {
    if (fold) return fold;
    const summary = el("summary", {}, "⚙ working…");
    const box = el("div", { class: "fold-body" });
    const details = el("details", { class: "work-fold" }, summary, box);
    const transcript = createTranscript(box, {
      answer: opts.answer, loadSub: opts.loadSub, isLive: opts.isLive });
    root.append(details);
    fold = { details, summary, transcript, steps: 0, briefs: [] };
    return fold;
  }

  function closeFold(status) {
    if (!fold) return;
    const kinds = fold.briefs.slice(-3).join(" · ");
    fold.summary.replaceChildren(
      `${status === "failed" ? "✕" : "⚙"} ${fold.steps} step${fold.steps === 1 ? "" : "s"}`
      + (kinds ? ` — ${kinds}` : ""));
    fold = null;
  }

  function replyNode(ev) {
    const p = ev.payload || {};
    let summary = p.summary || "";
    let topic = null;
    const m = NEW_TOPIC.exec(summary.split("\n")[0] || "");
    if (m) {
      topic = m[1].trim() || "new conversation";
      summary = summary.split("\n").slice(1).join("\n").trim();
    }
    const node = el("div", { class: "msg assistant" },
      topic ? el("div", { class: "topic-shift" },
        el("span", {}, "⤴ this looks like a new topic"),
        opts.onFork ? el("button", { class: "btn small",
          onclick: () => opts.onFork(topic, lastUser) }, `fork: ${topic}`) : null) : null,
      el("div", { class: "msg-body" }, md(summary || "(no reply text)")),
      el("div", { class: "msg-meta" },
        p.status && p.status !== "ok" ? el("span", { class: `chip ${p.status}` }, p.status) : null,
        el("span", {}, `${ev.turns ?? "?"} turns`),
        ev.usage_total ? el("span", {}, fmtTokens(ev.usage_total)) : null,
        ev.ts ? el("span", { title: ev.ts }, fmtTime(ev.ts)) : null));
    return node;
  }

  // One visible block per executed user command — compact per-kind rendering of the
  // observation payload (the transcript keeps the full record).
  function commandResult(p) {
    let body;
    if (p.error) body = `✕ ${p.error}`;
    else if (p.kind === "util") {
      body = (p.stdout || "(no stdout)") + (p.stderr ? `\n[stderr]\n${p.stderr}` : "");
      if (p.listing != null) body = p.listing;
      if (p.source != null) body = p.source;
    } else if (p.kind === "read_file") {
      body = p.files
        ? p.files.map((f) => `--- ${f.path}\n${f.error || f.content || ""}`).join("\n\n")
        : (p.content ?? "");
    } else if (p.kind === "view_image") {
      body = (p.files || []).map((f) => f.error ? `${f.path}: ${f.error}`
        : f.text ? `${f.path}:\n${f.text}` : `${f.path} — attached for the assistant`).join("\n\n");
    } else if (p.kind === "write_file") body = `wrote ${p.bytes} bytes to ${p.path}`;
    else if (p.kind === "edit_file") body = `replaced ${p.replacements} occurrence(s) in ${p.path}`;
    else if (p.kind === "llm") body = p.reply || "";
    else if (p.kind === "memory_read") body = p.missing ? `no note ${p.name}` : (p.content || "");
    else if (p.kind === "memory_write") body = `note ${p.name}.md ${p.deleted ? "deleted" : p.created ? "created" : "revised"}`;
    else body = JSON.stringify(p, null, 1);
    return el("div", { class: `ev cmd-result${p.error ? " err" : ""}` },
      el("pre", {}, String(body)));
  }

  function questionInline(ev) {
    const p = ev.payload;
    const head = el("div", { class: "msg-body" }, "❓ ", mdInline(p.question || ""),
      p.default ? el("div", { class: "faint small" }, `↪ without an answer: ${p.default}`) : null);
    if (!opts.answer || !p.qid) return head;
    const form = answerForm(p, {
      placeholder: "answer… (Shift+Enter for a new line)",
      defaultLine: false,   // the head already shows the default
      submitText: (text) => opts.answer(p.qid, text),
      onSuccess: (text) => form.setSettled(`✅ answered: ${text}`),
    });
    return el("div", {}, head, form.node);
  }

  return {
    add(ev) {
      const p = ev.payload || {};
      switch (ev.type) {
        case "header":
          return;
        case "user_injection":
          if (p.source === "engine") {
            closeFold("ok");
            root.append(el("div", { class: "ev system" }, `— ${p.text} —`));
          } else if (p.command) {
            // a slash command the user executed directly — mono bubble, result follows
            closeFold("ok");
            root.append(el("div", { class: "msg user cmd" },
              el("div", { class: "msg-body" }, p.text || "")));
          } else {
            closeFold("ok");
            lastUser = (p.text || "").replace(ATTACH_BLOCK, "").trim();
            root.append(userNode(p.text || ""));
          }
          return;
        case "finish":
          closeFold(p.status);
          root.append(replyNode(ev));
          return;
        case "assistant_action":
          if (p.kind === "finish") return;   // the finish EVENT carries the reply
          {
            const f = ensureFold();
            f.steps += 1;
            f.briefs.push(p.kind === "util" ? (p.name || "util") : p.kind);
            f.summary.replaceChildren(`⚙ working — ${f.steps} step${f.steps === 1 ? "" : "s"}`
              + (p.say ? " · " : ""), p.say ? mdInline(String(p.say).slice(0, 110)) : "");
            f.transcript.add(ev);
          }
          return;
        case "observation":
          if ((p.kind === "write_file" || p.kind === "edit_file") && !p.error
              && String(p.path || "").includes("artifacts/") && opts.onArtifact) {
            opts.onArtifact(p.path);
          }
          if (p.user_command) {         // the user asked for this result — show it, don't fold it
            closeFold("ok");
            root.append(commandResult(p));
            return;
          }
          if (fold) fold.transcript.add(ev);
          return;
        case "question":
          // questions surface at chat level — answerable where the model asked
          closeFold("ok");
          root.append(el("div", { class: "msg question-msg" }, questionInline(ev)));
          return;
        case "answer":
          root.append(el("div", { class: "ev answer" }, p.intermediate
            ? `💬 you (dialog): ${p.text}` : `✅ you answered: ${p.text}`));
          return;
        default:
          // errors, compaction, subrun_start/end — trace detail, into the fold
          ensureFold().transcript.add(ev);
      }
    },
    finishOpenFold() { closeFold("ok"); },
  };
}
