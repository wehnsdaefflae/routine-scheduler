// Decisions inbox: everything open across routines, blocking first. Keyboard-first —
// the first pending question autofocuses, Enter submits, ↑/↓ (or Tab) move between
// questions, and 1–9 pick a suggested option. Each question shows its routine, age,
// blocking/deferred badge, and (when run-bound) the run with its LIVE state, so a stale
// question left by a long-finished run is recognizable.

import { api } from "/static/api.js";
import { chip, el, emptyState, skeleton, toast, when } from "/static/util.js";

export async function render(view) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / decisions"),
      el("h1", {}, "Decisions"),
      el("div", { class: "sub" }, "answers the routines need from you — blocking ones first")),
    el("div", { class: "kbd-hint" },
      el("kbd", {}, "↵"), " answer · ", el("kbd", {}, "↑"), el("kbd", {}, "↓"), " move · ",
      el("kbd", {}, "1"), "–", el("kbd", {}, "9"), " pick option")));
  const list = el("div", { class: "mt" });
  list.append(skeleton(), skeleton());
  view.append(list);

  const inputs = [];   // answer inputs, in render order, for ↑/↓ focus moves

  function focusAt(i) {
    const input = inputs[Math.max(0, Math.min(inputs.length - 1, i))];
    if (input) { input.focus(); input.scrollIntoView({ block: "center", behavior: "smooth" }); }
  }

  async function load({ focus = true } = {}) {
    let qs;
    try { qs = await api("/api/questions"); }
    catch (err) { list.replaceChildren(emptyState("✕", "Couldn't load decisions", err.message)); return; }
    qs.sort((a, b) => (a.mode === "blocking" ? -1 : 1) - (b.mode === "blocking" ? -1 : 1));
    inputs.length = 0;
    list.replaceChildren();
    if (!qs.length) {
      list.append(emptyState("✓", "No decisions to make right now",
        "The routines are self-sufficient. Blocking questions pause their run here; deferred ones wait for the next run."));
      return;
    }
    qs.forEach((q, i) => list.append(item(q, i)));
    if (focus) focusAt(0);
  }

  function item(q, index) {
    const input = el("input", { type: "text", placeholder: "your answer…  (↵ to send)", style: "flex:1" });
    inputs.push(input);
    const send = el("button", { class: "btn primary" }, "answer");
    const options = q.options || [];
    const submit = async () => {
      if (!input.value.trim()) return;
      send.disabled = true;
      try {
        await api(`/api/questions/${q.qid}/answer`, { method: "POST", body: { text: input.value } });
        toast(q.mode === "blocking" ? "answered — the run resumes" : "answered — the next run picks it up");
        // Mark answered in place: a deferred question's pending file is only consumed when its
        // routine next runs, so a reload would still list it — that would read as "didn't work".
        panel.classList.remove("warn");
        controls.replaceChildren(el("div", { class: "flow-note" },
          chip("answered · queued", "ok"),
          el("span", {}, `“${input.value.trim()}” → inbox → consumed by the ${q.mode === "blocking" ? "waiting run" : "next run"}`)));
        inputs.splice(inputs.indexOf(input), 1);
        focusAt(index);          // move on to the next open question
      } catch (err) { toast(err.message, 4000, { error: true }); send.disabled = false; }
    };
    send.onclick = submit;
    input.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); submit(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); focusAt(index + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); focusAt(index - 1); }
      else if (/^[1-9]$/.test(e.key) && !input.value && options[+e.key - 1]) {
        e.preventDefault();
        input.value = options[+e.key - 1];
      }
    };
    const runBits = q.run_id ? [
      el("a", { class: "btn small", href: `#/run/${q.run_id}` }, "view run"),
      q.run_state ? chip(q.run_state, q.run_state) : null,
      q.run_state && ["finished", "failed", "aborted"].includes(q.run_state)
        ? el("span", { class: "faint small" }, "run already ended — the answer feeds the next one") : null,
    ] : [];
    const controls = el("div", {},
      options.length ? el("div", { class: "row mt", style: "gap:8px" },
        options.map((o, i) => el("button", { class: "btn small", title: `press ${i + 1}`,
          onclick: () => { input.value = o; input.focus(); } }, `${i + 1} · ${o}`))) : null,
      el("div", { class: "row mt" }, input, send));
    const panel = el("div", { class: `panel question-item${q.mode === "blocking" ? " warn" : ""}` },
      el("div", { class: "q-meta" },
        chip(q.mode, q.mode),
        el("a", { href: `#/routine/${q.routine}` }, q.routine),
        q.asked ? el("span", {}, "asked ", when(q.asked)) : null,
        ...runBits),
      el("div", { class: "q-text" }, q.question),
      controls);
    return panel;
  }

  await load();
  const onBus = () => load({ focus: false }).catch(() => {});
  window.addEventListener("rsched-bus", onBus);
  return () => window.removeEventListener("rsched-bus", onBus);
}
