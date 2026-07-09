// New-routine wizard: draft → clarify chat (a real engine run) → workflow pick → finalize.

import { api, sse } from "/static/api.js";
import { createTranscript } from "/static/components/transcript.js";
import { el, toast } from "/static/util.js";

export async function render(view, resumeWid) {
  view.append(el("h1", {}, "New routine"));
  const stage = el("div", {});
  view.append(stage);
  let source = null;

  if (resumeWid) stageChat(resumeWid);  // resume an existing session (replays the chat,
  else stageDraft();                    // jumps to suggest when the run is already done)

  function stageDraft() {
    stage.innerHTML = "";
    const ta = el("textarea", { class: "code", style: "min-height:160px",
      placeholder: "Describe what the routine should do, in your own words…\n\ne.g. Every Monday collect new AI-agent papers from arxiv and keep a reading list with one-line takes." });
    const go = el("button", { class: "btn primary" }, "start clarification");
    go.onclick = async () => {
      if (!ta.value.trim()) return;
      go.disabled = true;
      try {
        const r = await api("/api/wizard/start", { method: "POST", body: { draft: ta.value } });
        stageChat(r.wid);
      } catch (err) { toast(err.message); go.disabled = false; }
    };
    stage.append(el("div", { class: "panel" },
      el("div", { class: "muted", style: "margin-bottom:8px" },
        "The wizard interrogates your draft (an actual engine run of the clarify-instruction ",
        "workflow), then suggests a control-flow workflow and creates the routine."),
      ta, el("div", { class: "row mt" }, go)));
  }

  function stageChat(wid) {
    stage.innerHTML = "";
    stage.append(el("h2", {}, "Clarification — answer the questions"));
    const chatBox = el("div", { class: "mt" });
    const qBox = el("div", {});
    stage.append(qBox, chatBox);
    const transcript = createTranscript(chatBox);

    function showQuestion(q) {
      qBox.innerHTML = "";
      if (!q) return;
      const input = el("input", { type: "text", placeholder: "your answer…", style: "flex:1" });
      const send = el("button", { class: "btn primary" }, "answer");
      const submit = async () => {
        if (!input.value.trim()) return;
        try {
          await api(`/api/wizard/${wid}/answer`, { method: "POST",
            body: { qid: q.qid, text: input.value } });
          qBox.innerHTML = "";
        } catch (err) { toast(err.message); }
      };
      send.onclick = submit;
      input.onkeydown = (e) => { if (e.key === "Enter") submit(); };
      qBox.append(el("div", { class: "panel mt", style: "border-color:var(--warn)" },
        el("div", {}, `❓ ${q.question}`),
        q.options?.length ? el("div", { class: "row mt" },
          q.options.map((o) => el("button", { class: "btn small", onclick: () => { input.value = o; } }, o))) : null,
        el("div", { class: "row mt" }, input, send)));
      input.focus();
    }

    source = sse(`/api/wizard/${wid}/events`, {
      transcript: (ev) => transcript.add(ev),
      state: (st) => showQuestion(st.question),
      end: () => { source.close(); stageSuggest(wid); },
      onerror: () => {},
    });
  }

  async function stageSuggest(wid) {
    let data;
    try { data = await api(`/api/wizard/${wid}/suggest`, { method: "POST" }); }
    catch (err) {
      stage.append(el("div", { class: "panel mt", style: "border-color:var(--err)" },
        `clarify run ended without a result: ${err.message}`));
      return;
    }
    const wr = data.wizard_result;
    stage.innerHTML = "";
    stage.append(el("h2", {}, "Refined instruction"),
      el("pre", { class: "doc" }, wr.refined_instruction));

    const picked = { slug: data.suggestions[0]?.slug || "" };
    const picksRow = el("div", { class: "row mt" });
    const renderPicks = () => {
      picksRow.innerHTML = "";
      for (const s of data.suggestions) {
        picksRow.append(el("button", {
          class: `btn ${picked.slug === s.slug ? "primary" : ""}`,
          title: s.reason,
          onclick: () => { picked.slug = s.slug; renderPicks(); },
        }, `${s.slug} (${Math.round(s.confidence * 100)}%)`));
      }
      picksRow.append(genBtn);
    };
    const genBtn = el("button", { class: "btn" }, "✨ generate a new workflow");
    genBtn.onclick = async () => {
      genBtn.disabled = true; genBtn.textContent = "generating…";
      try {
        const r = await api(`/api/wizard/${wid}/generate-workflow`, { method: "POST",
          body: { hint: data.new_workflow_hint || "" } });
        data.suggestions.unshift({ slug: r.workflow_slug, confidence: 1, reason: "generated draft" });
        picked.slug = r.workflow_slug;
        toast(`draft workflow '${r.workflow_slug}' created in the library`);
      } catch (err) { toast(err.message, 6000); }
      genBtn.disabled = false; genBtn.textContent = "✨ generate a new workflow";
      renderPicks();
    };
    stage.append(el("h2", {}, "Workflow"),
      data.none_fit ? el("div", { class: "muted" }, `suggester: ${data.new_workflow_hint || "nothing fits well"}`) : null,
      picksRow);
    renderPicks();

    // Schedule is routine CONFIG, set here (or later on the routine page) — it is never
    // part of the instruction and never suggested by the model.
    const f = {
      slug: el("input", { type: "text", value: wr.suggested_slug || "" }),
      name: el("input", { type: "text", value: wr.suggested_name || "" }),
      cron: el("input", { type: "text", value: "", placeholder: "e.g. 0 7 * * 1 — empty = manual runs only" }),
      tz: el("input", { type: "text", value: "Europe/Berlin" }),
    };
    const runNow = el("input", { type: "checkbox", checked: true });
    const create = el("button", { class: "btn primary" }, "create routine");
    create.onclick = async () => {
      if (!picked.slug) { toast("pick a workflow"); return; }
      create.disabled = true;
      try {
        const r = await api(`/api/wizard/${wid}/finalize`, { method: "POST", body: {
          slug: f.slug.value.trim(), name: f.name.value.trim() || f.slug.value.trim(),
          workflow_slug: picked.slug, cron: f.cron.value.trim(), tz: f.tz.value.trim(),
          run_now: runNow.checked,
        }});
        toast(`routine ${r.slug} created`);
        location.hash = r.run_id ? `#/run/${r.run_id}` : `#/routine/${r.slug}`;
      } catch (err) { toast(err.message, 6000); create.disabled = false; }
    };
    stage.append(el("h2", {}, "Create"),
      el("div", { class: "panel" },
        el("div", { class: "field-row" },
          el("label", { class: "field" }, el("span", {}, "slug"), f.slug),
          el("label", { class: "field" }, el("span", {}, "name"), f.name)),
        el("div", { class: "field-row" },
          el("label", { class: "field" }, el("span", {}, "cron"), f.cron),
          el("label", { class: "field" }, el("span", {}, "timezone"), f.tz)),
        wr.notes ? el("div", { class: "muted" }, `wizard notes: ${wr.notes}`) : null,
        el("div", { class: "row mt" },
          el("label", { class: "row", style: "gap:4px" }, runNow, "first run immediately"),
          create)));
  }

  return () => { if (source) source.close(); };
}
