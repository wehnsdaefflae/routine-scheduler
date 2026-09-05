// One item card on the Messages page: a routine's finish SUMMARY, or a finding, decision or
// bug report with its status,
// prose, origin, and the changelog rows that addressed it. The card's DOM id is
// `ref-<ID>`, which is what reflinks.js focusRef lands on — every F/D/R mention anywhere
// in the console scrolls to (and flashes) the card built here.
//
// Findings carry the reviewer's comment box; decisions are read-only here (they are
// ANSWERED on the Decisions page, one inbox, meta-badged) and bug reports have no
// composer — they are evidence, not a conversation.

import { md, mdInline } from "/static/md.js";
import { chip, el, when } from "/static/util.js";

const STATUS_TONE = {
  open: "waiting_user", in_progress: "partial", addressed: "ok",
  settled: "ok", dropped: "idle", unknown: "",
};
const TYPE_LABEL = { finding: "finding", decision: "decision", report: "report",
                     summary: "summary" };
//: A run's outcome, shown on a summary card instead of a severity. `ok` is the ordinary case
//: and says nothing worth a chip; the other three are the reason you would read this one first.
const OUTCOME_TONE = { partial: "partial", failed: "err", aborted: "err" };
const SEV = ["problem", "systemic", "redundancy", "improvement", "info"];

function originLine(item) {
  const o = item.origin || {};
  const bits = [];
  if (o.routine) bits.push(el("span", {}, o.routine));
  if (o.run_id) bits.push(el("a", { href: `#/run/${o.run_id}`, title: "the run this item came from" }, "run ↗"));
  if (o.ts) bits.push(when(o.ts));
  if (o.commit) bits.push(el("span", { class: "faint" }, `since ${String(o.commit).slice(0, 8)}`));
  if (!bits.length) return null;
  const row = el("div", { class: "faint small mt", style: "display:flex;gap:8px;flex-wrap:wrap" },
    el("span", {}, "origin"));
  for (const b of bits) row.append(b);
  return row;
}

// The archive: which commits touched this item, newest first. A best-effort link came from
// an id scan of the row's prose (rows written before the explicit `items:` field) — labelled,
// so a wrong match is visible as a guess rather than read as a record.
function addressedSection(item) {
  const rows = item.addressed || [];
  if (!rows.length) return null;
  const guesses = rows.filter((r) => r.link !== "explicit").length;
  return el("details", { class: "mt" },
    el("summary", { class: "muted small" },
      `addressed · ${rows.length} change${rows.length === 1 ? "" : "s"}`,
      guesses ? ` (${guesses} matched by id scan)` : ""),
    ...rows.map((r) => el("div", { class: "addressed-item" },
      r.link === "explicit" ? chip("linked", "ok") : chip("best-effort", "partial"),
      el("span", { class: "p-text" }, mdInline(r.summary || r.title || "(change)")),
      r.commit ? el("span", { class: "faint small" }, String(r.commit).slice(0, 8)) : null,
      r.ts ? when(r.ts) : null)));
}

// An ADDRESSED report's routing: who sent it to whom, whether the target's run has actually
// drained it, and which reply closed it. This line is the whole point of the ledger — a
// hand-off that silently never arrives is worse than none. An unaddressed report (triage) has
// no routing to show.
function routingLine(item) {
  if (item.type !== "report" || !item.to) return null;
  const bits = [el("span", {}, `${item.origin?.routine || "?"} → ${item.to}`)];
  const d = item.delivered || {};
  if (d.run_id) {
    bits.push(el("a", { href: `#/run/${d.run_id}`, title: "the run that picked it up" }, "picked up ↗"));
    if (d.ts) bits.push(when(d.ts));
  } else if (item.retracted?.ts) {
    // the outbox's one write (docs/messages.md): withdrawn before delivery, never arrived
    bits.push(el("span", {}, "retracted — the target never saw it"), when(item.retracted.ts));
  } else {
    bits.push(el("span", { class: "faint" }, "not picked up yet — waits for the target's next run"));
  }
  // a closure (closes: true) is the exchange's terminal acknowledgment — born settled
  if (item.answers) bits.push(el("span", {}, item.closes ? `answers ${item.answers} — closes it` : `answers ${item.answers}`));
  if (item.answered_by) bits.push(el("span", {}, `answered by ${item.answered_by}`));
  const row = el("div", { class: "faint small mt", style: "display:flex;gap:8px;flex-wrap:wrap" },
    el("span", {}, "routing"));
  for (const b of bits) row.append(b);
  return row;
}

function refsLine(item) {
  if (!(item.refs || []).length) return null;
  return el("div", { class: "row mt", style: "gap:6px" },
    el("span", { class: "faint small" }, "refers to"),
    ...item.refs.map((r) => el("a", { class: "ref-link", href: `#/messages?focus=${r}` }, r)));
}

// `queued` is this finding's not-yet-consumed comment (if any): it persists in the box across
// reloads and stays editable — saving rewrites the SAME inbox message, not a new one.
function commentBox(item, queued, { onSave, onWithdraw }) {
  const note = el("textarea", { placeholder: "leave a comment on this finding…", rows: 2,
    style: "min-height:auto" });
  if (queued) note.value = queued.raw || "";
  const saveBtn = el("button", { class: "btn small primary" }, queued ? "update comment" : "send comment");
  saveBtn.onclick = async () => {
    if (!note.value.trim()) return;
    saveBtn.disabled = true;
    try { await onSave(note.value, queued); }
    finally { saveBtn.disabled = false; }
  };
  const dropBtn = !queued ? null
    : el("button", { class: "btn small ghost", title: "remove from the inbox — the run never sees it",
        onclick: async (e) => { e.target.disabled = true; await onWithdraw(queued, e.target); } }, "withdraw");
  return el("div", {},
    queued ? el("div", { class: "faint small mt" },
      "queued for the next run — edit or withdraw it until then") : null,
    el("div", { class: `row ${queued ? "" : "mt"}`, style: "gap:8px;align-items:flex-end" },
      el("div", { style: "flex:1" }, note), saveBtn, dropBtn));
}

export function itemCard(item, { queued, onSave, onWithdraw, answered, onPriority,
                                 onRead } = {}) {
  const status = item.status || "unknown";
  const isSummary = item.type === "summary";
  // An answered decision (durable marker, survives inbox consumption) reads as answered here
  // too — not re-presented as open once a run drains its feedback message.
  // A summary is not worked on, it is READ — so `open`/`settled` say the wrong thing on its
  // card even though they are the right thing in the store (a synonym in the vocabulary would
  // fork it; a synonym in the rendering costs nothing).
  const label = isSummary ? (status === "settled" ? "read" : "unread")
    : queued && item.type === "decision" ? "answer queued"
    : answered && status === "open" ? "answered" : status;
  const tone = label === "answer queued" ? "partial"
    : label === "unread" ? "waiting_user"
    : label === "read" ? "idle"
    : label === "answered" ? "ok" : (STATUS_TONE[status] ?? "");
  const sev = SEV.includes(item.severity) ? item.severity : "";
  // The ⚑ toggle: the user's "work this first" — floats the card on the page and the
  // OWNING routine's next run reads the flagged ids in its state digest (D75).
  const flag = !onPriority ? null : el("button", {
    class: `btn small ${item.priority ? "" : "ghost"}`,
    title: item.priority
      ? "unflag — stops floating this item and drops it from the owner's priority list"
      : "flag as priority — floats the card AND the owning routine's next run reads it first",
    onclick: async (e) => {
      e.target.disabled = true;
      try { await onPriority(!item.priority); } finally { e.target.disabled = false; }
    },
  }, item.priority ? "⚑ flagged" : "⚑");
  // Dismiss / undismiss. Not offered on a maintenance item: `priorities.ITEM_ID_RE` rejects a
  // run id by design, and a finding is settled by the work rather than by being looked at.
  const readBtn = !(isSummary && onRead) ? null : el("button", {
    class: `btn small ${status === "settled" ? "ghost" : ""}`,
    title: status === "settled"
      ? "mark unread — brings this routine's message back to the unread view"
      : "mark read — it comes back on its own when this routine finishes a newer run",
    onclick: async (e) => {
      e.target.disabled = true;
      try { await onRead(status !== "settled"); } finally { e.target.disabled = false; }
    },
  }, status === "settled" ? "unread" : "✓ read");
  const head = el("div", { class: "row spread" },
    el("div", { class: "row", style: "gap:9px" },
      chip(label, tone),
      item.priority ? chip("⚑ priority", "partial") : null,
      chip(TYPE_LABEL[item.type] || item.type, "idle"),
      sev ? chip(sev, `sev-${sev}`) : null,
      // a summary's outcome is the thing that decides whether you read it now
      isSummary && OUTCOME_TONE[item.outcome]
        ? chip(item.outcome, OUTCOME_TONE[item.outcome]) : null,
      el("strong", { class: "prose" }, item.title || item.id)),
    el("div", { class: "row", style: "gap:8px" }, isSummary ? readBtn : flag,
      isSummary
        ? el("a", { class: "faint small", href: `#/run/${item.id}` }, "open the run")
        : el("span", { class: "faint small" }, item.id)));

  const archiveNote = item.archive_only
    ? el("div", { class: "faint small mt" },
        "archive — no entry in the current report; what is known comes from the changelog below")
    : null;

  const decided = item.type === "decision" && ["settled", "dropped"].includes(status);
  return el("div", { class: "panel mt", id: `ref-${item.id}` },
    head,
    archiveNote,
    item.detail ? md(item.detail, "md mt prose") : null,
    (item.evidence || []).length ? el("div", { class: "row mt", style: "gap:6px" },
      el("span", { class: "faint small" }, "evidence"),
      ...item.evidence.map((e) => el("span", { class: "ref-tag" }, String(e)))) : null,
    (item.options || []).length && !decided
      ? el("div", { class: "faint small mt" }, `options: ${item.options.map(String).join("  ·  ")}`)
      : null,
    item.resolution ? el("div", { class: "muted small mt" }, `resolution: ${item.resolution}`) : null,
    routingLine(item),
    refsLine(item),
    originLine(item),
    addressedSection(item),
    item.type === "finding" && onSave ? commentBox(item, queued, { onSave, onWithdraw }) : null);
}
