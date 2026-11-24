// The PLAYGROUND — a Conversations-shaped page for features that are not production yet
// (operator, 2026-11-24: "offer another Conversations subpage in which experimental features
// are implemented to be tested").
//
// What it is, and what it is not. A playground conversation is an ORDINARY conversation — same
// engine, same workflow, same budgets — carrying `playground: true` and a list of experiment
// slugs. That is deliberate: an experiment has to run on the production code path or it tests
// something else. What the flag buys is that the engine's playground seam does anything at all;
// for every conversation without it the seam returns "" and the turn is byte-identical to today.
//
// Three things on the page, in the order they are useful:
//   1. the EXPERIMENTS on offer, each stating what it claims and which query recipe it uses;
//   2. a conversation list — start one with the experiments ticked, then talk to it normally;
//   3. the FIRINGS: what the layer actually surfaced in those runs, including the near-misses
//      that fell below the floor. That is the precision evidence, from real turns.
//
// Plus the retrieval bench (playground-bench.js) for the comparison that needs no conversation.
//
// Promotion to production is not a button here. When an experiment earns it, that is a code
// change someone makes on purpose — this page exists to tell you whether it earned it.

import { api } from "/static/api.js";
import { chip, el, emptyState, skeleton, toast, toastError } from "/static/util.js";
import { benchPanel } from "/static/views/playground-bench.js";

export async function render(view) {
  view.replaceChildren(skeleton(["30%", "100%", "100%", "60%"]));
  let meta;
  let convos;
  try {
    [meta, convos] = await Promise.all([
      api("/api/playground/features"),
      api("/api/playground/conversations"),
    ]);
  } catch (err) {
    view.replaceChildren(emptyState("⚠", "The playground could not load",
      String(err && err.message ? err.message : err)));
    return;
  }

  view.replaceChildren();
  view.append(header());
  view.append(experimentsPanel(meta, () => render(view)));
  view.append(conversationsPanel(convos, meta));
  view.append(benchPanel(meta));
}

function header() {
  return el("div", { style: "margin-bottom:14px" },
    el("div", { class: "muted small" },
      "Experimental features, tried on real conversations before any of them reaches ",
      "production. A playground conversation runs the ordinary engine — the experiments only ",
      "add text to what the run reads, and every addition is logged below so you can judge ",
      "whether it helped."));
}

function experimentsPanel(meta, reload) {
  const box = el("div", { class: "panel" });
  box.append(el("h3", {}, "Experiments"));
  const picked = new Set();
  for (const f of meta.features) {
    const tick = el("input", { type: "checkbox" });
    tick.onchange = () => (tick.checked ? picked.add(f.slug) : picked.delete(f.slug));
    box.append(el("div", { style: "padding:7px 0;border-top:1px solid var(--rule)" },
      el("label", { class: "row", style: "gap:8px;align-items:center" },
        tick, el("b", {}, f.title),
        chip(f.components || "no query"),
        ...(f.stores || []).map((s) => chip(s))),
      el("div", { class: "muted small", style: "margin:3px 0 0 26px" }, f.about)));
  }

  // The FLOOR, on the surface and tunable, because it is the open question the measurement
  // left: over 6,731 real shell/script turns the top util score had a median of 1.21 while the
  // highest scores were coincidences, and a true positive (a hand-rolled SMTP call matching
  // `mail`) scored 1.26 — below the noise's median. Whether any single threshold separates the
  // two is what these conversations are for, so the value is per-conversation rather than a
  // constant nobody can question.
  const floor = el("input", { type: "number", step: "0.1", min: "0", style: "width:72px",
    value: String(meta.min_score) });
  box.append(el("div", { class: "row small", style: "gap:8px;align-items:center;margin-top:10px" },
    el("span", { class: "muted" }, "score floor for this conversation:"), floor,
    el("span", { class: "faint" },
      "below it a hit is found but withheld — logged as `below_floor`, so you see the misses")));

  const message = el("textarea", { rows: 3, style: "flex:1",
    placeholder: "the first message — talk to it exactly as you would any conversation" });
  const start = el("button", { class: "btn small primary" }, "start conversation");
  start.onclick = async () => {
    const text = message.value.trim();
    if (!text) { toast("give it a first message"); return; }
    start.disabled = true;
    try {
      const res = await api("/api/playground/conversations",
        { method: "POST",
          body: { message: text, features: [...picked], min_score: Number(floor.value) } });
      toast(`playground conversation ${res.slug} started`);
      message.value = "";
      // The conversation itself is read and continued on the ordinary Conversations view —
      // the Playground does not reimplement the chat surface, it only decides what the run
      // runs with and shows what that produced.
      location.hash = `#/conversations/${res.slug}`;
    } catch (err) {
      toastError(err);
    } finally {
      start.disabled = false;
    }
  };
  box.append(el("div", { class: "row", style: "margin-top:12px;align-items:flex-start" },
    message, start));
  box.append(el("div", { class: "faint small", style: "margin-top:6px" },
    "Tick the experiments this conversation should run with. With none ticked you get a plain ",
    "conversation that is simply listed here — useful as the control."));
  if (reload) box.dataset.reload = "1";
  return box;
}

function conversationsPanel(convos, meta) {
  const box = el("div", { class: "panel" });
  box.append(el("h3", {}, `Playground conversations (${convos.conversations.length})`));
  if (!convos.conversations.length) {
    box.append(el("div", { class: "muted small" },
      "None yet. Start one above and it appears here with what its runs surfaced."));
    return box;
  }
  for (const c of convos.conversations) {
    const open = el("a", { href: `#/conversations/${c.slug}`, class: "small" }, c.name || c.slug);
    const firings = el("button", { class: "btn small" }, `firings (${c.firings})`);
    const detail = el("div", { style: "margin:4px 0 0 0" });
    firings.onclick = async () => {
      if (detail.childNodes.length) { detail.replaceChildren(); return; }
      detail.replaceChildren(el("div", { class: "muted small" }, "loading…"));
      try {
        const res = await api(`/api/playground/firings/${c.slug}`);
        detail.replaceChildren(firingsTable(res, meta));
      } catch (err) {
        toastError(err);
        detail.replaceChildren();
      }
    };
    box.append(el("div", { style: "padding:7px 0;border-top:1px solid var(--rule)" },
      el("div", { class: "row small", style: "gap:8px;align-items:center" },
        open,
        ...(c.features.length ? c.features.map((f) => chip(f)) : [chip("control — no features")]),
        el("span", { class: "muted" }, `${c.runs} run${c.runs === 1 ? "" : "s"}`),
        el("span", { style: "flex:1" }), firings),
      detail));
  }
  return box;
}

function firingsTable(res, meta) {
  const wrap = el("div");
  const counts = Object.entries(res.by_kind || {})
    .map(([k, n]) => `${k}: ${n}`).join("  ·  ");
  wrap.append(el("div", { class: "faint small", style: "margin:4px 0 6px" },
    counts || "nothing recorded",
    // Said on the surface, because it is the one thing that makes these numbers readable:
    // a `below_floor` row is a query whose best hit was NOT shown to the run. Those rows are
    // how the floor gets calibrated, and a layer that logged only its hits could not be judged.
    "   — `fired` was shown to the run; `below_floor` was found but withheld by the floor of ",
    String(meta.min_score)));
  if (!res.firings.length) {
    wrap.append(el("div", { class: "muted small" },
      "No firings yet — the conversation may not have reached a turn the experiments act on."));
    return wrap;
  }
  for (const f of res.firings.slice().reverse()) {
    const hit = f.hit || f.best;
    wrap.append(el("div", { class: "row small",
      style: "gap:8px;align-items:baseline;padding:2px 0;opacity:"
             + (f.kind === "fired" ? "1" : ".6") },
      el("span", { class: "ref-tag", style: "min-width:62px;text-align:center" },
        `t${f.turn ?? "?"}`),
      chip(f.feature || "—"),
      el("b", {}, f.kind || "?"),
      hit ? el("span", {}, `${hit.ident} ${Number(hit.score).toFixed(2)}`) : "",
      el("span", { class: "muted", style: "flex:1;min-width:0" },
        (f.query || f.error || "").slice(0, 120))));
  }
  return wrap;
}
