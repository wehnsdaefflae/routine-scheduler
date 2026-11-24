// A decision RENDERED THROUGH A2UI — D310 step 4, the console side.
//
// The three steps before this built the protocol: a surface store, two catalogs, a renderer for
// the full Basic Catalog. None of them was reachable from the console — they were exercised by
// their tests and by nothing else. This module is what closes that: it fetches one decision as an
// A2UI `createSurface` envelope (`GET /api/questions/{qid}/a2ui`), feeds it to the store exactly
// as a transport would feed an agent's stream, and mounts the renderer.
//
// WHY IT IS A SEPARATE SURFACE RATHER THAN A REPLACEMENT, today. The operator's order is that
// decision and ask elements use A2UI; his correction is that this must bring NEW interaction
// elements, not re-plumb the existing cards. Both are served by rendering the same decision
// through the protocol BESIDE the card, where the richer vocabulary is visible and comparable —
// chips with a filter instead of a row of buttons, a validated long-text field, the
// without-an-answer sentence in a modal. Replacing the card is the step AFTER this one is seen.
//
// THE ANSWER TRAVELS THE PROTOCOL'S CHANNEL AND LANDS IN THE ONE ANSWER ROUTE. The surface's
// submit is an A2UI agent EVENT (`answer_decision`) whose context carries the picked option and
// the typed text, resolved. This module turns that event into the same
// `POST /api/questions/{qid}/answer` call every other surface makes. There is no second answer
// shape and no second writer: `answerfile.write` is the only writer of the inbox's `answer-*`
// file, and a surface that posted its own body would be the thing that breaks that.
//
// WHAT AN EVENT THIS MODULE DOES NOT KNOW DOES. It is reported in place and nothing is posted —
// an agent naming an event the host has no handler for is a protocol mismatch, and the surface
// saying so is how that gets found. Silently swallowing it is how a decision looks answered and
// is not.

import { api } from "/static/api.js";
import { el } from "/static/util.js";
import { SurfaceStore } from "/static/a2ui/surface.js";
import { mountSurface } from "/static/a2ui/render.js";
import "/static/a2ui/widgets-layout.js";
import "/static/a2ui/widgets-input.js";

/** The event name the composed decision surface submits under (`web/a2ui_decision.py`). */
const ANSWER_EVENT = "answer_decision";

/** One decision's answer, as the answer route's body: the picked option, else the typed text.
 *
 * The picker's value is a LIST even when the variant is mutually exclusive (the catalog types it
 * `DynamicStringList`, so an agent reads one shape whichever variant it asked for) — so the
 * first entry is the choice. A typed answer beside a pick is appended, because a reader who
 * chose an option AND wrote a sentence meant both, and the answer route takes one `text`.
 */
export function answerBody(context) {
  const picked = [].concat(context?.picked ?? []).filter((v) => String(v ?? "").trim());
  const typed = String(context?.text ?? "").trim();
  if (!picked.length) return { text: typed };
  return { text: typed ? `${picked[0]} — ${typed}` : String(picked[0]) };
}

/** The handler for one surface's agent events, as a factory so it can be driven directly.
 *
 * It is exported for the same reason `answerBody` is: the two decisions worth testing here are
 * what an answer BODY becomes and what happens to an event this host does not handle, and the
 * second is unreachable through the mounted component (the surface the route composes only ever
 * sends `answer_decision`). A private closure would have left that path provable only by reading
 * the source, which proves nothing.
 *
 * `post` is the answer call, injectable for the same reason — the default is the one answer route
 * every other surface posts through, and there is no second answer shape anywhere in this file.
 */
export function decisionEvents(qid, { host, store, onAnswered = null, post = null } = {}) {
  const send = post ?? ((body) => api(
    `/api/questions/${encodeURIComponent(qid)}/answer`, { method: "POST", body }));
  return async (event) => {
    if (event.name !== ANSWER_EVENT) {
      host.append(el("div", { class: "a2-error", "data-code": "UNKNOWN_EVENT" },
                     `this surface sent "${event.name}", which this host does not handle`));
      return;
    }
    const body = answerBody(event.context);
    if (!body.text) return;            // the field's own @checks already say so on the surface
    try {
      await send(body);
      // The surface is deleted rather than left drawn: a decision that has been answered is not
      // a decision, and `deleteSurface` is the protocol's own way to say so — the renderer's
      // subscription repaints it empty.
      store.apply({ deleteSurface: { surfaceId: `decision:${qid}` } });
      host.append(el("div", { class: "a2-answered" }, `answered: ${body.text}`));
      if (onAnswered) onAnswered(body.text);
    } catch (error) {
      host.append(el("div", { class: "a2-error", "data-code": "ANSWER_FAILED" },
                     error?.message || "the answer was not accepted"));
    }
  };
}

/** The A2UI rendering of decision `qid`, as a node, with the surface live inside it.
 *
 * `onAnswered` is called with the posted text once the answer route accepts it, so the host can
 * close its card or resync its store — this module owns no page state.
 *
 * The node is returned EMPTY and fills when the fetch lands: a decisions page draws many cards
 * and must not wait on a request per card. A fetch that fails says so in place rather than
 * leaving an empty frame, which is the one thing a reader cannot tell from "no decision".
 */
export function a2uiDecision(qid, { onAnswered = null } = {}) {
  const host = el("div", { class: "a2-host", "data-a2-decision": String(qid) });
  const store = new SurfaceStore();
  let renderer = null;

  const fail = (message) => {
    host.replaceChildren(el("div", { class: "a2-error", "data-code": "FETCH_ERROR" }, message));
  };

  const onEvent = decisionEvents(qid, { host, store, onAnswered });

  (async () => {
    let message;
    try {
      message = await api(`/api/questions/${encodeURIComponent(qid)}/a2ui`);
    } catch (error) {
      fail(error?.message || "this decision could not be loaded as a surface");
      return;
    }
    try {
      store.apply(message);
      renderer = mountSurface(host, store, message.createSurface.surfaceId, { onEvent });
    } catch (error) {
      // A PROTOCOL error, which is worth showing with its code: it means the envelope the server
      // composed is not one this renderer accepts, and that is a defect in one of the two rather
      // than a problem with the decision.
      fail(`${error?.code || "PROTOCOL_ERROR"}: ${error?.message || "unrenderable surface"}`);
    }
  })();

  //: Dropping the node without this leaks the store subscription — one per decision drawn.
  host.teardown = () => renderer?.destroy();
  return host;
}

/** The decision's A2UI rendering as a FOLD, mounted the first time it is opened.
 *
 * This is how the Decisions page offers it: closed by default, because the card above it is what
 * the operator answers real work on and swapping that for a rendering he has not seen would be
 * the wrong order. Not a replacement — an addition, one click away.
 *
 * It lives HERE rather than in the view for the reason the fold exists at all: the lifecycle is
 * this module's business. The renderer subscribes to its surface store, so a fold opened on every
 * card of a long page leaks one subscription each unless the node is torn down — and the mount
 * must be LAZY, because a page of decisions must not fetch an envelope per card.
 *
 * `onAnswered` is the host's, passed straight through.
 */
export function a2uiFold(qid, { onAnswered = null } = {}) {
  const fold = el("details", { class: "q-a2ui" },
    el("summary", { class: "faint small" }, "render through A2UI"));
  let node = null;
  fold.addEventListener("toggle", () => {
    if (fold.open && !node) {
      node = a2uiDecision(qid, { onAnswered });
      fold.append(node);
      return;
    }
    if (!fold.open && node) {
      node.teardown?.();
      node.remove();
      node = null;
    }
  });
  return fold;
}
