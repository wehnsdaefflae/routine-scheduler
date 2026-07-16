// New-routine entry: describe the task, start the clarification. The session itself is a
// REAL run of the protected 'clarification' routine (D13=B), so starting — or resuming —
// one lands on the STANDARD run page (#/run/clarification:<ts>), where the setup panel
// (components/setuppanel.js) carries the flow through clarify → suggest → create → build.
// This view owns only the draft stage, plus the in-flight sessions to resume instead of
// forking a second one.

import { api } from "/static/api.js";
import { navigate } from "/static/router.js";
import { el, skeleton, toast } from "/static/util.js";

// Tell app.js a session started / was canceled so the setup banner updates at once.
const notifyChanged = () => window.dispatchEvent(new CustomEvent("rsched-wizard-changed"));

const STAGE_TEXT = { chat: "clarifying", suggest: "ready to create", building: "building the routine",
                     error: "needs attention" };

export async function render(view) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / new routine"),
      el("h1", {}, "New routine"))));
  const stage = el("div", {});
  view.append(stage);
  stage.append(skeleton(["100%", "60%"]));

  const st = await api("/api/status").catch(() => ({}));
  if (st.llm_ready === false) {
    stage.replaceChildren(el("div", { class: "panel warn" },
      el("strong", {}, "No model connected"),
      el("div", { class: "muted mt prose" },
        "Creating a routine runs a clarification through your LLM. Set the ",
        el("code", {}, "system model"), " in ", el("a", { href: "#/settings" }, "Settings"), " first.")));
    return;
  }

  stage.replaceChildren();
  const resumeBox = el("div", {});     // filled with any in-flight sessions to resume
  const ta = el("textarea", { class: "code", style: "min-height:160px",
    placeholder: "Describe the TASK the routine should do, in your own words — not when it runs.\n\ne.g. Collect new AI-agent papers from arxiv and keep a reading list with one-line takes." });
  const go = el("button", { class: "btn primary" }, "start clarification");
  go.onclick = async () => {
    if (!ta.value.trim()) return;
    go.disabled = true;
    try {
      const r = await api("/api/wizard/start", { method: "POST", body: { draft: ta.value } });
      notifyChanged();
      if (!r.run_id) {   // no clarification template on disk — a daemon boot seeds it
        toast("session started, but the clarification template routine is missing — restart the daemon to seed it", 7000, { error: true });
        go.disabled = false;
        return;
      }
      navigate(`#/run/${r.run_id}`);       // the run page is the session surface
    } catch (err) { toast(err.message, 4000, { error: true }); go.disabled = false; }
  };
  stage.append(resumeBox, el("div", { class: "panel" },
    el("div", { class: "muted prose", style: "margin-bottom:8px" },
      "Describe the task in your own words. The clarifier asks a few questions, then suggests ",
      "a workflow, the routine's traits (reusable practices, adapted into it at creation) and its ",
      "permissions (what it is allowed to do) — all reviewable before anything is created. Schedule ",
      "and models are set on the create page too."),
    ta, el("div", { class: "row mt" }, go)));

  // Surface any in-flight sessions so the user resumes instead of starting a second one.
  api("/api/wizard").then((list) => {
    if (!Array.isArray(list) || !list.length) return;
    const row = (w) => {
      const line = el("div", { class: "row spread", style: "padding:4px 0" },
        el("span", { class: "muted small", style: "min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" },
          `${STAGE_TEXT[w.stage] || w.stage} · ${w.draft || "(no description)"}`));
      // a pre-D13 session has no run page — the only surface left for it is cancel
      line.append(w.clarify_run_id
        ? el("a", { class: "btn small primary", href: `#/run/${w.clarify_run_id}` }, "resume")
        : el("button", { class: "btn small danger", onclick: async () => {
            try { await api(`/api/wizard/${encodeURIComponent(w.wid)}`, { method: "DELETE" }); } catch { /* gone */ }
            notifyChanged();
            line.remove();
          } }, "cancel"));
      return line;
    };
    resumeBox.append(el("div", { class: "panel warn", style: "margin-bottom:14px" },
      el("strong", {}, "Setup already in progress"),
      el("div", { class: "muted small", style: "margin:4px 0 8px" },
        "You have unfinished new-routine sessions — resume one instead of starting over:"),
      ...list.map(row)));
  }).catch(() => {});
}
