// The turn's PROMPT BY SEGMENT (D270) — one stacked bar in the run rail: what this run's
// context window is actually being spent on. The engine measures it beside the token-ratio
// calibration (engine/promptsegments.py, written by window.note_prompt_size) and status.json
// carries it as `prompt_segments`: the composer's own always-on sections in PROMPT ORDER
// (harness, schema, example, workflow, instruction, capabilities, digest, messages, reports),
// then the two segments the message list grows — `history`, what the model itself said, and
// `observations`, what the engine handed back.
//
// WHY A SEPARATE MODULE, and why it reads its own endpoint: views/run.js is frozen at its
// size-ratchet ceiling (583 lines), so the run page's rail composes itself in
// components/runrail.js, and a section that needs the run detail either waits for the view to
// hand it over — a line run.js has no room for — or reads it itself. This reads it itself, the
// way components/planstrip.js and components/fileactivity.js do, and polls while the run is
// live so the bar tracks the window as the turn count climbs.
//
// LAZY, like `oversight`, `jobs` and `scripts`: a run recorded before D270 (or one whose
// provider never reported a prompt size, so nothing calibrated) has no breakdown at all, and
// gains no caption — tests/ui/test_finish_line.py pins the rail's caption list exactly, and an
// always-present empty card teaches a reader to skip the one place the window is explained.
//
// The colours are base.css's --series-1..6 tokens, handed out as var() references so an SVG
// fill follows a theme switch with no re-render (the convention components/charts.js
// established and tests/test_stylesheet_tokens.py checks the inputs of). Unlike charts.js the
// palette CYCLES here: there is no entity whose colour must stay stable across a re-render —
// the segment list is fixed and ordered, so a colour is a LOCATOR into the legend beside it,
// never the datum. The datum is the legend's token count and share.

import { api } from "/static/api.js";
import { openModal } from "/static/components/dialog.js";
import { el, fmtNum, svgEl } from "/static/util.js";

const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)",
                "var(--series-4)", "var(--series-5)", "var(--series-6)"];
const BAR_H = 14;        // the bar's own height, in the viewBox's units and in px
// The sub-run tree's interval, deliberately: tests/ui/test_view_teardown.py's QUIET_MS (4500)
// is the window in which a leaked poller must be SEEN to fire, so a slower poll than the rail's
// others would pass that pin while still leaking.
const POLL_MS = 3000;

//: The two segments that are not a composer section but the message list's own halves —
//: named apart in the legend because they are the ones a run can do something about.
const GROWN = { history: "what this run said", observations: "what it was handed back" };

/** Open ONE segment's text — what a click on a rect or a legend row does (R3341).
 *
 * Two segments have recorded text and the rest do not, and the modal says which it is holding
 * rather than implying it: `history` and `observations` ARE the message list and are replayed
 * from the transcript through the engine's own renderers, while every always-on section answers
 * "text not recorded — the prompt is measured, never stored". The server decides that, not this
 * module (readmodels/promptsegtext), so the view cannot offer a click it would refuse.
 *
 * Rows first, bodies on demand: `history` and `observations` run to hundreds of thousands of
 * tokens on a long run, and this console is read on a phone. Each row carries the turn the
 * transcript stamped and a token figure that SUMS to the segment total the bar draws, so the
 * view is auditable against the bar rather than merely adjacent to it.
 *
 * Returns the modal's close(), which the caller keeps so a rail teardown ends it too.
 */
async function openSegment(runId, name, anchorNode) {
  const head = el("div", {}, el("b", {}, name));
  const body = el("div", { class: "faint small" }, "loading\u2026");
  const close = el("button", { class: "btn" }, "close");
  const panel = el("div", { class: "panel", style: "max-width:46rem" }, head, body,
    el("div", { class: "row mt", style: "justify-content:flex-end" }, close));
  // `openModal` puts the overlay on document.body, so the rail leaving the document does NOT
  // remove it — and this module's stop() is not a reliable hook either: views/run.js is at its
  // size-ratchet ceiling and has no room to call it (this file's own header says so). So the
  // panel watches the SAME liveness signal every poller here uses, its anchor's connectivity,
  // and dismisses itself when the bar it belongs to is gone. Measured: without this, a reader
  // who navigated to another page kept a dialog about a run they had left — the browser test
  // caught it, and the view's DOM showed the panel still open on #/help.
  const WATCH_MS = 400;
  const watch = setInterval(() => {
    if (!anchorNode || !anchorNode.isConnected) dismiss();
  }, WATCH_MS);
  const closeModal = openModal(panel, { label: head, focus: close,
                                        onCancel: () => dismiss() });
  function dismiss() { clearInterval(watch); closeModal(); }
  close.onclick = () => dismiss();
  let page;
  try { page = await api(`/api/runs/${runId}/prompt-segment?name=${encodeURIComponent(name)}`); }
  catch { body.replaceChildren(el("div", { class: "faint small" }, "could not be read")); return dismiss; }
  const note = el("div", { class: "faint small" }, page.note || "");
  if (!page.recorded) { body.replaceChildren(note); return dismiss; }
  const rows = el("div", {});
  (page.rows || []).forEach((r) => {
    const open = el("button", { class: "btn" },
      `turn ${r.turn} · ${fmtNum(r.tokens)} tokens`);
    const text = el("pre", { class: "doc", hidden: true });
    open.onclick = async () => {
      if (!text.hidden) { text.hidden = true; return; }
      if (!text.textContent) {
        try {
          const one = await api(`/api/runs/${runId}/prompt-segment`
                                + `?name=${encodeURIComponent(name)}&i=${r.i}`);
          text.textContent = one.text || "";
        } catch { text.textContent = "could not be read"; }
      }
      text.hidden = false;
    };
    rows.append(el("div", { class: "mt" }, open, el("div", { class: "faint small" }, r.preview),
                   text));
  });
  body.replaceChildren(note,
    el("div", { class: "faint small mt" },
      `${page.total} messages · ${fmtNum(page.tokens)} estimated tokens, newest first`),
    rows);
  return dismiss;
}


/** The bar plus its legend, or null when there is nothing measured to show. Not exported: it
 * is this module's own drawing, and an export no other module imports is a symbol the codemap
 * reports as rot. The browser test reads the rendered DOM, which is the claim that matters. */
function segmentBar(segments, onPick) {
  const rows = Object.entries(segments || {})
    .map(([name, tokens]) => [name, Number(tokens) || 0])
    .filter(([, tokens]) => tokens > 0);
  const total = rows.reduce((sum, [, tokens]) => sum + tokens, 0);
  if (!total) return null;

  const svg = svgEl("svg", { viewBox: `0 0 100 ${BAR_H}`, width: "100%",
                             height: String(BAR_H), preserveAspectRatio: "none", role: "img",
                             "aria-label": `this turn's prompt by segment, ${fmtNum(total)} `
                                         + "estimated tokens in total" });
  const legend = el("div", { class: "faint small" });
  let x = 0;
  rows.forEach(([name, tokens], i) => {
    const share = (tokens / total) * 100;
    const color = COLORS[i % COLORS.length];
    const pct = share >= 1 ? Math.round(share) : share.toFixed(1);
    const what = GROWN[name] ? ` — ${GROWN[name]}` : "";
    // A sliver under half a percent is not drawable, so it is given no rect at all and is
    // read off the legend instead: a 0-width rect would claim the bar is complete when the
    // segment it stands for is missing from it.
    if (share >= 0.5)
      svg.append(svgEl("rect", { x: String(x), y: "0", width: String(share),
                                 height: String(BAR_H), fill: color },
                       `${name}${what}: ${fmtNum(tokens)} tokens · ${pct}%`));
    x += share;
    // THE CLICK the operator asked for lives on the legend row, not on the rect: a sliver under
    // half a percent has no rect at all (above), and a 0.5%-wide rect is not a target a thumb
    // can hit. A `btn` carries it, so there is no new class to style and the row is focusable
    // and keyboard-operable for free.
    legend.append(el("div", {},
      el("span", { style: `color:${color}`, "aria-hidden": "true" }, "\u25a0 "),
      onPick
        ? el("button", { class: "btn", "data-segment": name, onclick: () => onPick(name) },
             `${name} · ${fmtNum(tokens)} · ${pct}%`)
        : `${name} · ${fmtNum(tokens)} · ${pct}%`));
  });
  return el("div", { "data-prompt-segments": String(rows.length) },
    svg,
    el("div", { class: "faint small" },
      `${fmtNum(total)} estimated tokens, in prompt order`),
    legend);
}

/**
 * Add the `prompt` rail section for a run that HAS a breakdown, and keep it current while the
 * run is live. Adds nothing at all otherwise. Returns the poller's stop(), so a view that
 * tears the rail down can end it; the poller is self-defending either way (it stops as soon
 * as its own node has left the document, the guard every rail poller here carries).
 */
export function mountPromptSegments(rail, { runId, isLive, host }) {
  // The body is parked in the rail's HOST straight away, hidden and captionless, and `rail.add`
  // later MOVES it under its own caption. That is what makes `isConnected` a true liveness
  // signal from the first tick: a body that only enters the document once there is data would
  // read as "still connected" forever for a run that has none, and the poller would outlive
  // the view — `isLive()` reads a state variable frozen at whatever it held when the view was
  // torn down, so it cannot stop anything (tests/ui/test_view_teardown.py pins that no request
  // keyed to a run survives leaving it, and views/run.js is at its ceiling with no room for a
  // stop() call of its own).
  // `host` is the rail's own element, never a document-level fallback: document.body is never
  // disconnected, so parking there would read as "still mounted" for the life of the tab and
  // defeat the guard this parking exists to arm.
  const body = el("div", { hidden: true });
  host.append(body);
  let stopped = false;
  let timer = null;
  let added = false;
  // The open segment modal's dismiss, if one is open. It lives on document.body (openModal's
  // overlay does), so it is NOT torn down by the rail leaving the document — `stop()` has to
  // dismiss it, or a reader who navigates away keeps a dialog about a run they have left
  // (tests/ui/test_view_teardown.py's rule, applied to a node outside the rail).
  let closeOpen = () => {};

  async function refresh() {
    let detail;
    // BEFORE the request, not after it: the only thing that stops this poller is its own node
    // leaving the document, so a connectivity check that runs on the ANSWER has already asked
    // for a run nobody is looking at (tests/ui/test_prompt_segment_bar.py's teardown case
    // caught exactly that).
    if (stopped || !body.isConnected) return;
    try { detail = await api(`/api/runs/${runId}`); }
    catch { return; }                  // transient: keep the last bar rather than blanking it
    if (stopped || !body.isConnected) return;
    const bar = segmentBar(detail.prompt_segments, async (name) => {
      closeOpen();                     // one segment at a time; a second click replaces the first
      closeOpen = await openSegment(runId, name, body) || closeOpen;
    });
    if (!bar) return;                  // nothing measured yet — and so still no caption
    body.replaceChildren(bar);
    if (!added) { rail.add("prompt", body); added = true; }
  }

  function poll() {
    if (timer) clearTimeout(timer);
    timer = null;
    if (stopped || !body.isConnected) return;
    if (isLive && isLive()) timer = setTimeout(async () => {
      if (stopped || !body.isConnected) return;
      await refresh();
      poll();
    }, POLL_MS);
  }

  refresh().then(poll);
  return { stop() {
    stopped = true;
    if (timer) clearTimeout(timer);
    timer = null;
    closeOpen();
  } };
}
