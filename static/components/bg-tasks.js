// The conversation rail's BACKGROUND section: one row per detached task this conversation
// owns (the `detach` action, or the composer's ⇥ background control), live while it runs and
// carrying its DELIVERY once it has one.
//
// D308 pt 2. It lived inside views/conversations.js as a paint + a 15 s poll, which is why
// the row said only "state · label": a task's elapsed time, whether its result ever reached
// this conversation (`delivered`, served all along by api_background.list_background_rows and
// rendered nowhere), the task's own run page and the files it landed under
// `artifacts/from-bg-<taskid>/` were all invisible. That file is frozen at its ratchet
// ceiling, so the section moved out whole rather than growing there.
//
// REFRESH IS EVENT-DRIVEN, not timed. The poll was armed by CONTENT (rows that exist, or a
// run that could create some) and still asked a question nobody had an answer to every 15 s
// for as long as a task ran. The daemon already publishes `run_started` / `run_finished` for
// a detached run exactly as for any other (daemon/runner.py), so the rail listens for the
// ones that belong to it and reads once, when something actually changed. The only timer left
// is the one that makes a live row's elapsed time TICK — it writes to the DOM and asks the
// server nothing, and it stops the moment no row is live.

import { api } from "/static/api.js";
import { chip, el, fmtDur, toDate, toast, toastError } from "/static/util.js";

//: Task states that mean the job is still going — a row with one gets the cancel control and
//: a ticking elapsed time. Mirrors the conversation view's own live/terminal vocabulary
//: (static/states.js) for a TASK, whose "pending" (no run yet) and "queued" (no dir yet) are
//: not run states at all.
const LIVE = new Set(["pending", "queued", "starting", "running", "waiting_user", "paused"]);

/** True when `runId` names a run of one of `slugs`, or of a task this conversation OWNS.
 *
 * A run id is `<slug>:<ts>` (ids.run_id). The known slugs come from the rows already read, so
 * a task the rail has never seen — the one a reply detached a moment ago — would not match
 * any of them; its id still names its owner, because `ids.background_task_id` builds every
 * taskid as `bg-<owner slug>-<random>`. Matching both is what makes the first event about a
 * new task arrive instead of waiting for something else to trigger a read.
 */
export function ownsRun(runId, slugs, ownerSlug) {
  const slug = String(runId || "").split(":")[0];
  if (!slug) return false;
  return slugs.includes(slug) || slug.startsWith(`bg-${ownerSlug}-`);
}

/** The rail's background section, mounted into `body` (the rail body) and shown only once
 * there is a task.
 *
 * `rail` is the shared rail component (components/rail.js) — the section hides itself again
 * when the last task is gone. `openArtifact(path)` opens ONE landed file in the artifacts
 * panel, the same way a hand-back card's file chip does (components/handback-card.js): the
 * file route needs the console's auth header, so a plain anchor cannot fetch it.
 */
export function createBackgroundTasks(body, { slug, rail, openArtifact }) {
  let rows = [];
  let tick = null;

  // ---- one row -------------------------------------------------------------------------------
  function elapsedNode(t) {
    const since = toDate(t.started);
    if (!since) return null;
    const node = el("span", { class: "bg-task-elapsed", title: "running for" });
    node.dataset.since = String(since.getTime());
    paintElapsed(node);
    return node;
  }

  function deliveryNode(t) {
    // The flag is about the HAND-BACK, not about the run: a task can finish and still owe its
    // result to this conversation (the daemon delivers on its next tick, and a delivery whose
    // owner is gone is recorded as dropped). Saying "finished" for both hid exactly that gap.
    const delivered = Boolean(t.delivered);
    return el("span", { class: `bg-task-delivery${delivered ? " on" : ""}`,
                        title: delivered
                          ? "its result was handed back into this conversation"
                          : "finished — its result has not reached this conversation yet" },
              delivered ? "delivered" : "awaiting delivery");
  }

  function runLink(t) {
    if (!t.run_id) return null;
    return el("a", { class: "bg-task-run", href: `#/run/${t.run_id}`,
                     title: `the task's own run page (${t.run_id})` }, "run");
  }

  function fileNodes(t) {
    return (t.files || []).map((path) => el("button", {
      type: "button", class: "attach-chip", title: path,
      onclick: () => openArtifact?.(path),
    }, "📎 ", String(path).split("/").pop()));
  }

  function cancelButton(t) {
    const btn = el("button", { class: "bg-cancel", title: "cancel this background task" }, "✕");
    btn.onclick = async () => {
      btn.disabled = true;
      try { await api(`/api/conversations/${slug}/background/${t.taskid}/cancel`, { method: "POST" }); }
      catch (err) { toastError(err); btn.disabled = false; return; }
      toast("cancelling background task…");
      setTimeout(refresh, 800);
    };
    return btn;
  }

  function taskNode(t) {
    const live = LIVE.has(t.state);
    const top = el("div", { class: "bg-task" },
      chip(t.state, t.state),
      el("span", { class: "bg-task-label", title: t.summary || "" }, t.label),
      live ? cancelButton(t) : null);
    const meta = el("div", { class: "bg-task-meta" },
      live ? elapsedNode(t) : deliveryNode(t),
      runLink(t),
      ...(t.delivered ? fileNodes(t) : []));
    return el("div", { class: "bg-task-card", "data-taskid": t.taskid }, top, meta);
  }

  // ---- the ticking elapsed time ---------------------------------------------------------------
  function paintElapsed(node) {
    const since = Number(node.dataset.since || 0);
    node.textContent = since ? fmtDur((Date.now() - since) / 1000) : "";
  }

  function armTick() {
    const live = rows.some((t) => LIVE.has(t.state) && t.started);
    if (live && !tick) {
      tick = setInterval(() => {
        for (const n of body.querySelectorAll(".bg-task-elapsed")) paintElapsed(n);
      }, 1000);
    } else if (!live && tick) {
      clearInterval(tick);
      tick = null;
    }
  }

  // ---- painting + reading ---------------------------------------------------------------------
  function paint(next) {
    rows = Array.isArray(next) ? next : [];
    body.replaceChildren(...rows.map(taskNode));
    rail.toggle("background", rows.length > 0);
    armTick();
  }

  let reading = false;
  async function refresh() {
    if (reading) return;      // a burst of events about the same task is one read
    reading = true;
    try { paint(await api(`/api/conversations/${slug}/background`)); }
    catch { /* transient — the last good render stands */ }
    finally { reading = false; }
  }

  // ---- the bus -------------------------------------------------------------------------------
  const onBus = (e) => {
    const ev = e.detail || {};
    if (ev.event !== "run_started" && ev.event !== "run_finished") return;
    if (!ownsRun(ev.run_id, rows.map((t) => t.taskid), slug)) return;
    refresh();
  };
  window.addEventListener("rsched-bus", onBus);

  return {
    paint,
    refresh,
    /** Stop every timer and listener this section owns — the view's teardown calls it. */
    stop() {
      window.removeEventListener("rsched-bus", onBus);
      if (tick) clearInterval(tick);
      tick = null;
    },
  };
}
