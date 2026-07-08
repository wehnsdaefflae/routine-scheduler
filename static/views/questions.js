// Questions inbox: everything open across routines, blocking first.

import { api } from "/static/api.js";
import { chip, el, toast } from "/static/util.js";

export async function render(view) {
  view.append(el("h1", {}, "Open questions"));
  const list = el("div", { class: "mt" });
  view.append(list);

  async function load() {
    const qs = await api("/api/questions");
    qs.sort((a, b) => (a.mode === "blocking" ? -1 : 1) - (b.mode === "blocking" ? -1 : 1));
    list.innerHTML = "";
    if (!qs.length) {
      list.append(el("div", { class: "empty" }, "Nothing to answer — the routines are self-sufficient right now."));
      return;
    }
    for (const q of qs) list.append(item(q));
  }

  function item(q) {
    const input = el("input", { type: "text", placeholder: "your answer…", style: "flex:1" });
    const send = el("button", { class: "btn primary" }, "answer");
    send.onclick = async () => {
      if (!input.value.trim()) return;
      send.disabled = true;
      try {
        await api(`/api/questions/${q.qid}/answer`, { method: "POST", body: { text: input.value } });
        toast(q.mode === "blocking" ? "answered — the run resumes" : "answered — next run picks it up");
        await load();
      } catch (err) { toast(err.message); send.disabled = false; }
    };
    return el("div", { class: "panel mt", style: q.mode === "blocking" ? "border-color:var(--warn)" : "" },
      el("div", { class: "row spread" },
        el("div", {},
          el("a", { href: `#/routine/${q.routine}` }, q.routine), " ",
          chip(q.mode, q.mode === "blocking" ? "waiting_user" : ""),
          q.run_id ? el("a", { class: "btn small", style: "margin-left:8px", href: `#/run/${q.run_id}` }, "run") : null),
        el("span", { class: "muted" }, q.asked || "")),
      el("div", { class: "mt" }, q.question),
      q.options?.length ? el("div", { class: "row mt" },
        q.options.map((o) => el("button", { class: "btn small", onclick: () => { input.value = o; } }, o))) : null,
      el("div", { class: "row mt" }, input, send));
  }

  await load();
  const onBus = () => load().catch(() => {});
  window.addEventListener("rsched-bus", onBus);
  return () => window.removeEventListener("rsched-bus", onBus);
}
