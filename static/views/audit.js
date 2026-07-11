// Audit: the self-audit routine's report on the scheduler itself. Renders the changelog,
// findings (comment box each), decisions (options + free text), and a general note box.
// The feedback loop is explicit: everything submitted lands in the routine's inbox and is
// consumed by its next run — the "waiting for the next run" list shows exactly what's queued,
// and a decision with queued feedback is marked answered (vs still open).

import { api } from "/static/api.js";
import { chip, el, emptyState, fmtTs, skeleton, toast, when } from "/static/util.js";

const SEV = ["problem", "systemic", "redundancy", "improvement", "info"];

export async function render(view) {
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

  // ---- sections ------------------------------------------------------------
  function pendingSection(pending) {
    if (!pending.length) return null;
    return el("div", {},
      el("h2", {}, `Waiting for the next run · ${pending.length}`),
      el("div", { class: "panel" },
        el("div", { class: "muted small", style: "margin-bottom:4px" },
          "feedback already in the routine's inbox — it is consumed (and disappears from here) when the next self-audit run starts"),
        ...pending.map((p) => el("div", { class: "pending-item" },
          chip("queued", "waiting_user"),
          el("span", { class: "p-text" }, p.text),
          p.ts ? when(p.ts) : null))));
  }

  function changelogSection(entries) {
    const items = (entries || []).length
      ? entries.map((c) => el("div", { class: "panel mt" },
          el("div", { class: "row spread" },
            el("strong", { class: "prose" }, c.summary || "(change)"),
            el("span", { class: "muted small" },
              c.ts ? when(c.ts) : null,
              c.commit ? ` · ${String(c.commit).slice(0, 8)}` : "")),
          c.detail ? el("div", { class: "muted mt prose", style: "white-space:pre-wrap" }, c.detail) : null))
      : [el("div", { class: "muted small", style: "padding:6px 0" },
          "No self-modifications recorded yet.")];
    return el("div", {}, el("h2", {}, "Changelog"), ...items);
  }

  function findingPanel(f) {
    const sev = SEV.includes(f.severity) ? f.severity : "info";
    const note = el("textarea", { placeholder: "leave a comment on this finding…", rows: 2, style: "min-height:auto" });
    const saveBtn = el("button", { class: "btn small primary" }, "send comment");
    saveBtn.onclick = async () => {
      if (!note.value.trim()) return;
      saveBtn.disabled = true;
      try { await submit({ kind: "comment", target: f.id, text: note.value }, "comment sent"); }
      catch (err) { toast(err.message, 4000, { error: true }); }
      finally { saveBtn.disabled = false; }
    };
    return el("div", { class: "panel mt" },
      el("div", { class: "row spread" },
        el("div", { class: "row", style: "gap:9px" }, chip(sev, `sev-${sev}`),
          el("strong", { class: "prose" }, f.title || f.id)),
        el("span", { class: "faint small" }, f.id)),
      f.detail ? el("div", { class: "mt prose", style: "white-space:pre-wrap" }, f.detail) : null,
      (f.evidence || []).length ? el("div", { class: "row mt", style: "gap:6px" },
        el("span", { class: "faint small" }, "evidence"),
        ...f.evidence.map((e) => el("span", { class: "ref-tag" }, String(e)))) : null,
      el("div", { class: "row mt", style: "gap:8px;align-items:flex-end" },
        el("div", { style: "flex:1" }, note), saveBtn));
  }

  function decisionPanel(d, queuedText) {
    let selected = null;
    const buttons = (d.options || []).map((o) => {
      const b = el("button", { class: "btn small" }, o);
      b.onclick = () => {
        selected = selected === o ? null : o;
        buttons.forEach((x) => x.classList.toggle("primary", x === b && selected === o));
      };
      return b;
    });
    const free = el("input", { type: "text", placeholder: "…or type your own answer", style: "flex:1" });
    const sendBtn = el("button", { class: "btn small primary" }, queuedText ? "replace answer" : "submit");
    sendBtn.onclick = async () => {
      if (!selected && !free.value.trim()) { toast("pick an option or type an answer"); return; }
      sendBtn.disabled = true;
      try { await submit({ kind: "decision", target: d.id, choice: selected, text: free.value }, "decision recorded"); }
      catch (err) { toast(err.message, 4000, { error: true }); }
      finally { sendBtn.disabled = false; }
    };
    return el("div", { class: `panel mt${queuedText ? "" : " warn"}` },
      el("div", { class: "row spread" },
        el("div", { class: "row", style: "gap:9px" },
          queuedText ? chip("answered · queued", "ok") : chip("open", "blocking"),
          el("strong", { class: "prose" }, d.title || d.id)),
        el("span", { class: "faint small" }, d.id)),
      d.detail ? el("div", { class: "mt prose", style: "white-space:pre-wrap" }, d.detail) : null,
      queuedText ? el("div", { class: "flow-note" },
        el("span", { class: "arrow" }, "→"),
        el("span", {}, `your answer is queued: ${queuedText}`)) : null,
      el("div", { class: "row mt", style: "gap:8px" }, ...buttons),
      el("div", { class: "row mt", style: "gap:8px" }, free, sendBtn));
  }

  function generalSection(routineSlug) {
    const box = el("textarea", { class: "code",
      placeholder: "e.g. “add structured logging to the daemon runner”, or a priority/direction — a free-text prompt for the next self-audit run to act on" });
    const send = el("button", { class: "btn primary mt" }, "send to the next run");
    send.onclick = async () => {
      if (!box.value.trim()) return;
      send.disabled = true;
      try { await submit({ kind: "general", text: box.value }, "prompt sent"); box.value = ""; }
      catch (err) { toast(err.message, 4000, { error: true }); }
      finally { send.disabled = false; }
    };
    // Fires self-audit immediately; an unsent note is delivered first so it isn't lost —
    // the fresh run drains the inbox at boot and reads it.
    const runNow = el("button", { class: "btn mt" }, "▶ run self-audit now");
    runNow.onclick = async () => {
      runNow.disabled = send.disabled = true;
      try {
        if (box.value.trim()) {
          await submit({ kind: "general", text: box.value }, "prompt sent");
          box.value = "";
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
        box, el("div", { class: "row", style: "gap:8px" }, send, runNow),
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
    // which decision ids already have an answer queued in the inbox
    const queuedDecisions = new Map();
    for (const p of pending) {
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
      if (r.summary) body.append(el("div", { class: "panel prose" }, r.summary));
    }

    const pendingBox = pendingSection(pending);
    if (pendingBox) body.append(pendingBox);

    body.append(changelogSection(data.changelog));

    if (r) {
      const findings = r.findings || [];
      body.append(el("h2", {}, `Findings${findings.length ? ` · ${findings.length}` : ""}`));
      body.append(findings.length ? el("div", {}, ...findings.map(findingPanel))
        : el("div", { class: "muted small", style: "padding:6px 0" }, "No findings this run — all clear."));
      const decisions = r.decisions || [];
      if (decisions.length) {
        const open = decisions.filter((d) => !queuedDecisions.has(d.id)).length;
        body.append(el("h2", {}, `Decisions for you · ${open} open / ${decisions.length - open} answered`));
        body.append(el("div", {}, ...decisions.map((d) => decisionPanel(d, queuedDecisions.get(d.id)))));
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
  }

  await load();
}
