// Audit: the self-audit routine's report on the scheduler itself. Renders the changelog,
// findings (each with a comment box), decisions (clickable options + free-text alternative),
// and a general note box. Every submission is written to the routine's inbox and considered
// on its next run. Reads /api/audit; writes via /api/audit/feedback.

import { api } from "/static/api.js";
import { chip, el, fmtTime, fmtTs, toast } from "/static/util.js";

const SEV = ["problem", "systemic", "redundancy", "improvement", "info"];

export async function render(view) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("h1", {}, "Audit"),
      el("div", { class: "muted", style: "font-size:13px" },
        "the self-audit routine's read on routine-scheduler itself")),
    el("div", { class: "row" }, el("button", { class: "btn small", onclick: () => load() }, "↻ refresh"))));

  const body = el("div", {});
  view.append(body);

  async function submit(payload, okMsg) {
    const r = await api("/api/audit/feedback", { method: "POST", body: payload });
    toast(r.delivery === "mid-run" ? `${okMsg} — picked up this run` : `${okMsg} — queued for the next run`);
  }

  // ---- sections ------------------------------------------------------------
  function changelogSection(entries) {
    const items = (entries || []).length
      ? entries.map((c) => el("div", { class: "panel mt" },
          el("div", { class: "row spread" },
            el("strong", {}, c.summary || "(change)"),
            el("span", { class: "muted", style: "font-family:var(--mono);font-size:12px" },
              [c.ts ? fmtTime(c.ts) : "", c.commit ? `· ${String(c.commit).slice(0, 8)}` : ""].filter(Boolean).join(" "))),
          c.detail ? el("div", { class: "muted mt", style: "white-space:pre-wrap" }, c.detail) : null))
      : [el("div", { class: "muted", style: "font-family:var(--mono);font-size:12.5px;padding:6px 0" },
          "No self-modifications recorded yet.")];
    return el("div", {}, el("h2", {}, "Changelog"), ...items);
  }

  function findingPanel(f) {
    const sev = SEV.includes(f.severity) ? f.severity : "info";
    const note = el("textarea", { placeholder: "leave a comment on this finding…", rows: 2, style: "min-height:auto" });
    const saveBtn = el("button", { class: "btn small primary" }, "save comment");
    saveBtn.onclick = async () => {
      if (!note.value.trim()) return;
      saveBtn.disabled = true;
      try { await submit({ kind: "comment", target: f.id, text: note.value }, "comment sent"); note.value = ""; }
      catch (err) { toast(err.message); }
      finally { saveBtn.disabled = false; }
    };
    return el("div", { class: "panel mt" },
      el("div", { class: "row spread" },
        el("div", { class: "row", style: "gap:9px" }, chip(sev, `sev-${sev}`),
          el("strong", {}, f.title || f.id)),
        el("span", { class: "muted", style: "font-family:var(--mono);font-size:11px" }, f.id)),
      f.detail ? el("div", { class: "mt", style: "white-space:pre-wrap" }, f.detail) : null,
      (f.evidence || []).length ? el("div", { class: "row mt", style: "gap:6px" },
        el("span", { class: "muted", style: "font-family:var(--mono);font-size:11px" }, "evidence"),
        ...f.evidence.map((e) => el("span", { class: "ref-tag" }, String(e)))) : null,
      el("div", { class: "row mt", style: "gap:8px;align-items:flex-end" },
        el("div", { style: "flex:1" }, note), saveBtn));
  }

  function decisionPanel(d) {
    let selected = null;
    const opts = el("div", { class: "row mt", style: "gap:8px" });
    const buttons = (d.options || []).map((o) => {
      const b = el("button", { class: "btn small" }, o);
      b.onclick = () => {
        selected = selected === o ? null : o;
        buttons.forEach((x) => x.classList.toggle("primary", x === b && selected === o));
      };
      return b;
    });
    opts.append(...buttons);
    const free = el("input", { type: "text", placeholder: "…or type your own answer", style: "flex:1" });
    const sendBtn = el("button", { class: "btn small primary" }, "submit");
    sendBtn.onclick = async () => {
      if (!selected && !free.value.trim()) { toast("pick an option or type an answer"); return; }
      sendBtn.disabled = true;
      try {
        await submit({ kind: "decision", target: d.id, choice: selected, text: free.value }, "decision recorded");
        free.value = ""; selected = null; buttons.forEach((x) => x.classList.remove("primary"));
      } catch (err) { toast(err.message); }
      finally { sendBtn.disabled = false; }
    };
    return el("div", { class: "panel mt", style: "border-color:var(--amber-dim)" },
      el("div", { class: "row spread" },
        el("strong", {}, d.title || d.id),
        el("span", { class: "muted", style: "font-family:var(--mono);font-size:11px" }, d.id)),
      d.detail ? el("div", { class: "mt", style: "white-space:pre-wrap" }, d.detail) : null,
      opts,
      el("div", { class: "row mt", style: "gap:8px" }, free, sendBtn));
  }

  function generalSection() {
    const box = el("textarea", { class: "code",
      placeholder: "e.g. “add structured logging to the daemon runner”, or a priority/direction — a free-text prompt for the next self-audit run to act on" });
    const send = el("button", { class: "btn primary mt" }, "send to the next run");
    send.onclick = async () => {
      if (!box.value.trim()) return;
      send.disabled = true;
      try { await submit({ kind: "general", text: box.value }, "prompt sent"); box.value = ""; }
      catch (err) { toast(err.message); }
      finally { send.disabled = false; }
    };
    return el("div", {}, el("h2", {}, "Note for the next run"),
      el("div", { class: "panel" },
        el("div", { class: "muted", style: "font-family:var(--mono);font-size:12px;margin-bottom:8px" },
          "a prompt the self-audit routine reads on its next run — code changes to make, priorities, or anything not tied to a finding/decision above"),
        box, el("div", { class: "row" }, send)));
  }

  // ---- load ----------------------------------------------------------------
  async function load() {
    body.innerHTML = "";
    let data;
    try { data = await api("/api/audit"); }
    catch (err) { body.append(el("div", { class: "empty" }, `couldn't load the audit: ${err.message}`)); return; }

    if (!data.exists) {
      body.append(el("div", { class: "empty" },
        "The self-audit routine isn't set up yet. Once it's created and has run, its findings and decisions appear here."));
      return;
    }
    const r = data.report;
    if (r) {
      const meta = [r.since?.window, r.generated ? `generated ${fmtTime(r.generated)}` : "",
                    r.since?.commit ? `since ${String(r.since.commit).slice(0, 8)}` : ""].filter(Boolean).join("  ·  ");
      if (meta) body.append(el("div", { class: "muted", style: "font-family:var(--mono);font-size:12px;margin-bottom:4px" }, meta));
      if (r.summary) body.append(el("div", { class: "panel" }, r.summary));
    }

    body.append(changelogSection(data.changelog));

    if (r) {
      const findings = r.findings || [];
      body.append(el("h2", {}, `Findings${findings.length ? ` · ${findings.length}` : ""}`));
      body.append(findings.length ? el("div", {}, ...findings.map(findingPanel))
        : el("div", { class: "muted", style: "font-family:var(--mono);font-size:12.5px;padding:6px 0" }, "No findings this run — all clear."));
      const decisions = r.decisions || [];
      if (decisions.length) {
        body.append(el("h2", {}, `Decisions for you · ${decisions.length}`));
        body.append(el("div", {}, ...decisions.map(decisionPanel)));
      }
    } else {
      body.append(el("div", { class: "empty", style: "padding:22px 0" },
        data.last_run
          ? `No report yet — the last run (${fmtTs(data.last_run.ts)}) is ${data.last_run.state}. Leave a prompt below; the next run picks it up.`
          : "No report yet — the self-audit routine runs on its schedule (or hit ▶ run now on it). You can already leave a prompt below for its first run."));
    }

    // The note field is always available while the routine exists — leave a prompt any time,
    // report or not.
    body.append(generalSection());
  }

  await load();
}
