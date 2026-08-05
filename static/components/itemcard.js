// One item card on the Items page: a finding, decision or bug report with its status,
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
const TYPE_LABEL = { finding: "finding", decision: "decision", report: "report" };
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
    ...item.refs.map((r) => el("a", { class: "ref-link", href: `#/items?focus=${r}` }, r)));
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

export function itemCard(item, { queued, onSave, onWithdraw, answered } = {}) {
  const status = item.status || "unknown";
  // An answered decision (durable marker, survives inbox consumption) reads as answered here
  // too — not re-presented as open once a run drains its feedback message.
  const label = queued && item.type === "decision" ? "answer queued"
    : answered && status === "open" ? "answered" : status;
  const tone = label === "answer queued" ? "partial"
    : label === "answered" ? "ok" : (STATUS_TONE[status] ?? "");
  const sev = SEV.includes(item.severity) ? item.severity : "";
  const head = el("div", { class: "row spread" },
    el("div", { class: "row", style: "gap:9px" },
      chip(label, tone),
      chip(TYPE_LABEL[item.type] || item.type, "idle"),
      sev ? chip(sev, `sev-${sev}`) : null,
      el("strong", { class: "prose" }, item.title || item.id)),
    el("span", { class: "faint small" }, item.id));

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
