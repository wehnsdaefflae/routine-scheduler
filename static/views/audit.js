// Audit: the self-audit routine's report on the scheduler itself. Renders the changelog,
// findings (comment box each), decisions (options + free text), and a general note box.
// The feedback loop is explicit: everything submitted lands in the routine's inbox and is
// consumed by its next run — the "waiting for the next run" list shows exactly what's queued.
// Queued feedback stays LIVE until that consumption: a finding's queued comment reappears in
// its comment box for in-place editing, and every queued item can be edited or withdrawn
// from the pending list. A decision with queued feedback is marked answered (vs still open).

import { api } from "/static/api.js";
import { md } from "/static/md.js";
import { chip, el, emptyState, fmtTs, skeleton, toast, when } from "/static/util.js";
import { focusRef, linkifyRefs } from "/static/components/reflinks.js";
import { forgetField } from "/static/formpersist.js";

const SEV = ["problem", "systemic", "redundancy", "improvement", "info"];
const isSettled = (d) => String(d.status || "").toLowerCase() === "settled"
  || String(d.detail || "").trimStart().toUpperCase().startsWith("SETTLED");

export async function render(view, query = {}) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / self-audit"),
      el("h1", {}, "Audit"),
      el("div", { class: "sub" }, "the self-audit routine's read on routine-scheduler itself")),
    el("div", { class: "row" }, el("button", { class: "btn small", onclick: () => load() }, "↻ refresh"))));

  const body = el("div", {});
  body.append(skeleton());
  view.append(body);

  // Feedback → routine inbox → consumed by the next (or current) self-audit run. After a
  // submit we say where it went and refresh the pending list, so the loop is visible.
  async function submit(payload, okMsg) {
    const r = await api("/api/audit/feedback", { method: "POST", body: payload });
    toast(r.delivery === "mid-run"
      ? `${okMsg} → inbox → the RUNNING self-audit picks it up this run`
      : `${okMsg} → inbox → consumed by the next self-audit run`, 4200);
    await load();
  }

  async function updateFeedback(id, payload, okMsg) {
    await api(`/api/audit/feedback/${encodeURIComponent(id)}`, { method: "PUT", body: payload });
    toast(`${okMsg} — still queued for the next run`);
    await load();
  }
  async function withdrawFeedback(id) {
    await api(`/api/audit/feedback/${encodeURIComponent(id)}`, { method: "DELETE" });
    toast("withdrawn — the run won't see it");
    await load();
  }

  // ---- sections ------------------------------------------------------------
  // Each queued item is live until a run drains the inbox: editable in place (the structured
  // kind/target/choice ride along server-side, so saving re-formats cleanly) and withdrawable.
  function pendingRow(p) {
    const row = el("div", { class: "pending-item" });
    const drop = el("button", { class: "btn small ghost", title: "remove from the inbox — the run never sees it" }, "withdraw");
    drop.onclick = async () => {
      drop.disabled = true;
      try { await withdrawFeedback(p.id); }
      catch (err) { toast(err.message, 4000, { error: true }); drop.disabled = false; }
    };
    const edit = p.kind ? el("button", { class: "btn small ghost" }, "edit") : null;
    if (edit) edit.onclick = () => {
      const ta = el("textarea", { rows: 2, style: "min-height:auto;flex:1" });
      ta.value = p.raw || "";
      const save = el("button", { class: "btn small primary" }, "save");
      save.onclick = async () => {
        if (!ta.value.trim() && !p.choice) return;   // a decision may stand on its choice alone
        save.disabled = true;
        try { await updateFeedback(p.id, { kind: p.kind, target: p.target, choice: p.choice, text: ta.value }, "updated"); }
        catch (err) { toast(err.message, 4000, { error: true }); save.disabled = false; }
      };
      const cancel = el("button", { class: "btn small ghost", onclick: () => load() }, "cancel");
      row.replaceChildren(chip("queued", "waiting_user"), ta, save, cancel, drop);
      ta.focus();
    };
    row.append(chip("queued", "waiting_user"), el("span", { class: "p-text" }, p.text),
      p.ts ? when(p.ts) : null, edit, drop);
    return row;
  }

  function pendingSection(pending) {
    if (!pending.length) return null;
    return el("div", {},
      el("h2", {}, `Waiting for the next run · ${pending.length}`),
      el("div", { class: "panel" },
        el("div", { class: "muted small", style: "margin-bottom:4px" },
          "feedback already in the routine's inbox — editable and withdrawable right here until a self-audit run consumes it (then it disappears from this list)"),
        ...pending.map(pendingRow)));
  }

  function changelogSection(entries) {
    const items = (entries || []).length
      ? entries.map((c) => el("div", { class: "panel mt" },
          el("div", { class: "row spread" },
            el("strong", { class: "prose" }, c.summary || "(change)"),
            el("span", { class: "muted small" },
              c.ts ? when(c.ts) : null,
              c.commit ? ` · ${String(c.commit).slice(0, 8)}` : "")),
          c.detail ? md(c.detail, "md muted mt prose") : null))
      : [el("div", { class: "muted small", style: "padding:6px 0" },
          "No self-modifications recorded yet.")];
    return el("div", {}, el("h2", {}, "Changelog"), ...items);
  }

  // `queued` is this finding's not-yet-consumed comment (if any): it persists in the box
  // across reloads and stays editable — saving rewrites the SAME inbox message, not a new one.
  function findingPanel(f, queued) {
    const sev = SEV.includes(f.severity) ? f.severity : "info";
    const note = el("textarea", { placeholder: "leave a comment on this finding…", rows: 2, style: "min-height:auto" });
    if (queued) note.value = queued.raw || "";
    const saveBtn = el("button", { class: "btn small primary" }, queued ? "update comment" : "send comment");
    saveBtn.onclick = async () => {
      if (!note.value.trim()) return;
      saveBtn.disabled = true;
      const payload = { kind: "comment", target: f.id, text: note.value };
      try {
        if (queued) await updateFeedback(queued.id, payload, "comment updated");
        else await submit(payload, "comment sent");
      }
      catch (err) { toast(err.message, 4000, { error: true }); }
      finally { saveBtn.disabled = false; }
    };
    const dropBtn = !queued ? null
      : el("button", { class: "btn small ghost", title: "remove from the inbox — the run never sees it",
          onclick: async (e) => {
            e.target.disabled = true;
            try { await withdrawFeedback(queued.id); }
            catch (err) { toast(err.message, 4000, { error: true }); e.target.disabled = false; }
          } }, "withdraw");
    return el("div", { class: "panel mt", id: `ref-${f.id}` },
      el("div", { class: "row spread" },
        el("div", { class: "row", style: "gap:9px" }, chip(sev, `sev-${sev}`),
          el("strong", { class: "prose" }, f.title || f.id)),
        el("span", { class: "faint small" }, f.id)),
      f.detail ? md(f.detail, "md mt prose") : null,
      (f.evidence || []).length ? el("div", { class: "row mt", style: "gap:6px" },
        el("span", { class: "faint small" }, "evidence"),
        ...f.evidence.map((e) => el("span", { class: "ref-tag" }, String(e)))) : null,
      queued ? el("div", { class: "faint small mt" },
        "queued for the next run — edit or withdraw it until then") : null,
      el("div", { class: `row ${queued ? "" : "mt"}`, style: "gap:8px;align-items:flex-end" },
        el("div", { style: "flex:1" }, note), saveBtn, dropBtn));
  }

  // Decisions are ANSWERED on the Decisions page (one inbox, meta-badged) — here each
  // gets a read-only card (the landing target for D-references) plus the status line.
  function decisionsSummary(decisions, queuedDecisions, answeredDecisions) {
    const open = decisions.filter((d) => !queuedDecisions.has(d.id)
      && !answeredDecisions.has(d.id) && !isSettled(d)).length;
    const queued = decisions.filter((d) => queuedDecisions.has(d.id)).length;
    return el("div", {},
      el("h2", {}, `Decisions · ${open} open / ${queued} queued / ${decisions.length} total`),
      el("div", { class: "panel" }, el("div", { class: "flow-note" },
        open
          ? el("span", {}, `${open} decision${open === 1 ? "" : "s"} await${open === 1 ? "s" : ""} you — `)
          : el("span", {}, "nothing awaits you — settled and queued decisions are recorded in the report. "),
        open ? el("a", { class: "btn small", href: "#/questions" }, "answer on the Decisions page") : null,
        el("span", {}, " Answers flow back to the next run as settled work orders."))));
  }

  function decisionPanel(d, queuedDecisions, answeredDecisions) {
    // An answered decision (durable marker, survives inbox consumption) reads as answered
    // here too — not re-presented as "open" once a run drains its feedback message.
    const [label, tone] = isSettled(d) ? ["settled", "ok"]
      : answeredDecisions.has(d.id) ? ["answered", "ok"]
      : queuedDecisions.has(d.id) ? ["answer queued", "partial"] : ["open", "waiting_user"];
    const decided = isSettled(d) || answeredDecisions.has(d.id);
    return el("div", { class: "panel mt", id: `ref-${d.id}` },
      el("div", { class: "row spread" },
        el("div", { class: "row", style: "gap:9px" }, chip(label, tone),
          el("strong", { class: "prose" }, d.title || d.id)),
        el("span", { class: "faint small" }, d.id)),
      d.detail ? md(d.detail, "md muted mt prose") : null,
      (d.options || []).length && !decided
        ? el("div", { class: "faint small mt" }, `options: ${d.options.map(String).join("  ·  ")}`)
        : null);
  }

  function generalSection(routineSlug) {
    // data-persist gives the draft an explicit storage key (a fresh one — drafts stranded
    // under the pre-forgetField key stop reappearing); discard clears a stale draft in one click.
    const box = el("textarea", { class: "code", "data-persist": "audit-note",
      placeholder: "e.g. “add structured logging to the daemon runner”, or a priority/direction — a free-text prompt for the next self-audit run to act on" });
    const discard = el("button", { class: "btn small mt", title: "clear this draft — nothing is sent" }, "discard draft");
    discard.onclick = () => { box.value = ""; forgetField(box); };
    const send = el("button", { class: "btn primary mt" }, "send to the next run");
    send.onclick = async () => {
      const text = box.value;
      if (!text.trim()) return;
      send.disabled = true;
      // Clear the draft BEFORE submit()'s reload re-mounts the box — otherwise formpersist
      // refills the fresh (empty) box from the not-yet-forgotten draft and it looks unsent.
      box.value = ""; forgetField(box);
      try { await submit({ kind: "general", text }, "prompt sent"); }
      catch (err) { box.value = text; toast(err.message, 4000, { error: true }); }
      finally { send.disabled = false; }
    };
    // Fires self-audit immediately; an unsent note is delivered first so it isn't lost —
    // the fresh run drains the inbox at boot and reads it.
    const runNow = el("button", { class: "btn mt" }, "▶ run self-audit now");
    runNow.onclick = async () => {
      runNow.disabled = send.disabled = true;
      const text = box.value;
      try {
        if (text.trim()) {
          box.value = "";
          forgetField(box);   // clear BEFORE submit()'s reload re-mounts the box (else it refills)
          await submit({ kind: "general", text }, "prompt sent");
        }
        const r = await api(`/api/routines/${routineSlug}/run`, { method: "POST" });
        toast("self-audit started");
        location.hash = `#/run/${r.run_id}`;
      } catch (err) { toast(err.message, 5000, { error: true }); }
      finally { runNow.disabled = send.disabled = false; }
    };
    return el("div", {}, el("h2", {}, "Note for the next run"),
      el("div", { class: "panel" },
        el("div", { class: "muted small", style: "margin-bottom:8px" },
          "a prompt the self-audit routine reads on its next run — code changes to make, priorities, or anything not tied to a finding/decision above"),
        box, el("div", { class: "row", style: "gap:8px" }, send, runNow, discard),
        el("div", { class: "flow-note" },
          el("span", {}, "submit"), el("span", { class: "arrow" }, "→"),
          el("span", {}, "routine inbox"), el("span", { class: "arrow" }, "→"),
          el("span", {}, "consumed at the start of the next self-audit run — or immediately via ▶"))));
  }

  // ---- load ----------------------------------------------------------------
  async function load() {
    let data;
    try { data = await api("/api/audit"); }
    catch (err) { body.replaceChildren(emptyState("✕", "Couldn't load the audit", err.message)); return; }
    body.replaceChildren();

    if (!data.exists) {
      body.append(emptyState("◌", "The self-audit routine isn't set up yet",
        "Once it's created (it ships with the install, under the meta tag) and has run, its findings and decisions appear here."));
      return;
    }
    const pending = data.pending_feedback || [];
    // which decision ids already have an answer queued, and which findings a comment —
    // structured fields when present, text parse for messages queued before they existed
    const queuedDecisions = new Map();
    const queuedComments = new Map();   // finding id → latest queued comment (oldest first, so last wins)
    for (const p of pending) {
      if (p.kind === "decision" && p.target) { queuedDecisions.set(p.target, p.raw); continue; }
      if (p.kind === "comment" && p.target) { queuedComments.set(p.target, p); continue; }
      const m = /^\[AUDIT decision · ([^\]]+)\]\s*(.*)$/.exec(p.text || "");
      if (m) queuedDecisions.set(m[1].trim(), m[2]);
    }

    const r = data.report;
    if (r) {
      const meta = el("div", { class: "muted small", style: "margin-bottom:4px" });
      if (r.since?.window) meta.append(`${r.since.window}  ·  `);
      if (r.generated) meta.append("generated ", when(r.generated));
      if (r.since?.commit) meta.append(`  ·  since ${String(r.since.commit).slice(0, 8)}`);
      body.append(meta);
      if (r.summary) body.append(md(r.summary, "md panel prose"));
    }

    const pendingBox = pendingSection(pending);
    if (pendingBox) body.append(pendingBox);

    body.append(changelogSection(data.changelog));

    if (r) {
      const findings = r.findings || [];
      body.append(el("h2", {}, `Findings${findings.length ? ` · ${findings.length}` : ""}`));
      body.append(findings.length
        ? el("div", {}, ...findings.map((f) => findingPanel(f, queuedComments.get(f.id))))
        : el("div", { class: "muted small", style: "padding:6px 0" }, "No findings this run — all clear."));
      const decisions = r.decisions || [];
      if (decisions.length) {
        // Durable answered markers (from decisions-answered.json) — the Decisions page uses
        // the same set to hide answered decisions; the Audit page must agree so a decision
        // answered elsewhere doesn't re-present as open here after a run consumes its message.
        const answeredDecisions = new Set(data.answered_decisions || []);
        body.append(decisionsSummary(decisions, queuedDecisions, answeredDecisions),
          ...decisions.map((d) => decisionPanel(d, queuedDecisions, answeredDecisions)));
      }
    } else {
      body.append(data.last_run
        ? emptyState("▢", "No report from the last run",
            `The last run (${fmtTs(data.last_run.ts)} · ${data.last_run.state}) produced no report. Leave a prompt below; the next run picks it up.`)
        : emptyState("◌", "Never ran",
            "The self-audit routine runs on its schedule (or hit ▶ run now on its page). You can already leave a prompt below for its first run."));
    }

    // The note field is always available while the routine exists — leave a prompt any time.
    body.append(generalSection(data.routine));
    // every F63/D14 mention in the report's prose becomes a link to its card above
    linkifyRefs(body);
  }

  await load();
  // arriving via a ref link (#/audit?focus=F63): land on the named card and flash it
  if (query.focus) focusRef(String(query.focus));
}
