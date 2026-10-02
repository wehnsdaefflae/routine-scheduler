// Resilient transcript tail: REST catch-up for an authoritative byte offset, then a live
// WebSocket tail from that offset. When the stream dies, we back off (with jitter — correlated
// drops must not reconnect in lockstep), catch up over REST (skipping events the dead stream
// already delivered), and reopen the stream at the new offset — nothing is lost or duplicated.
// This generalizes the log view's "poll as fallback" pattern for the run view and the chat.
//
// Every tail holds its own socket. Tails used to be rationed to three, the rest falling back to
// REST polling, because each one was an EventSource holding one of the ~6 HTTP/1.1 connections a
// browser keeps per origin (F263, F606). They are WebSockets now, which the browser counts
// against a separate, far larger limit, so a live view never has to wait for a slot.

import { api, liveStream, openStreamCount } from "/static/api.js";

const MAX_BACKOFF_MS = 15000;

// page(offset)   → REST path returning {events, offset}
// events(offset) → live-stream path emitting `transcript` / `state` / `end`
// onStatus(s)    → "live" | "reconnecting" | "ended"
// onGone()       → the resource 404'd (session archived / run pruned): stop for good
export function liveTail({ page, events, offset = 0, onEvent, onState, onStatus, onEnd, onGone }) {
  let base = offset;       // last byte offset confirmed by a REST page
  let seen = 0;            // events delivered by the stream since `base` (skip on catch-up)
  let source = null, timer = null, retry = 0, stopped = false, ended = false;
  let openedAt = 0, seenSinceOpen = 0;   // F175: how long each stream lived + what it carried

  const status = (s) => { if (!stopped && onStatus) onStatus(s); };
  const close = () => {
    if (source) { try { source.close(); } catch { /* already closed */ } source = null; }
  };

  async function catchUp() {
    const { events: evs, offset: next } = await api(page(base));
    // The view may have stopped this tail while the page was in flight — and its onEvent acts
    // on the WINDOW (a view following the newest message scrolls it to the bottom), so a page
    // delivered now would scroll whatever page replaced it.
    if (stopped) return;
    for (const ev of evs.slice(seen)) onEvent(ev);
    base = next;
    seen = 0;
  }

  function open() {
    if (stopped || ended) return;
    source = liveStream(events(base), {
      transcript: (ev) => { retry = 0; seen += 1; seenSinceOpen += 1; onEvent(ev); },
      state: (s) => { retry = 0; if (onState) onState(s); },
      end: () => { ended = true; close(); status("ended"); if (onEnd) onEnd(); },
      onopen: () => { openedAt = Date.now(); seenSinceOpen = 0; status("live"); },
      onerror: () => {
        if (stopped || ended) return;
        const streamsAtDrop = openStreamCount();   // BEFORE close() uncounts the dying stream
        close();
        reconnect(streamsAtDrop);
      },
    });
  }

  function reconnect(streamsAtDrop = openStreamCount()) {
    status("reconnecting");
    if (retry === 0) {
      // first drop only — backoff retries of the same outage aren't new friction evidence.
      // The detail records the stream's age and traffic (F175: run-view streams die every
      // ~2min — age/traffic tells an idle-timeout kill from a mid-burst one).
      // F263: stamp the concurrent open-stream count — a reconnect burst under a high stream
      // count is the signature to look for when the console stalls.
      const detail = (openedAt
        ? `alive ${Math.round((Date.now() - openedAt) / 1000)}s, ${seenSinceOpen} events`
        : "before first open") + `, ${streamsAtDrop} streams open`;
      import("/static/trace.js").then(({ trace }) => trace("reconnect", events(base), detail)).catch(() => {});
    }
    // jitter (±25%): tails killed together (a daemon restart, a network blip) must not
    // thunder back in lockstep
    const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** retry) * (0.75 + Math.random() * 0.5);
    retry += 1;
    timer = setTimeout(kick, delay);
  }

  async function kick() {
    if (stopped || ended) return;
    try { await catchUp(); } catch (err) {
      if (stopped) return;   // stopped while the page was in flight: nobody to report to
      if (err.status === 404) { stopped = true; if (onGone) onGone(); return; }
      reconnect();
      return;
    }
    open();
  }

  // The browser knows when connectivity returns — skip the remaining backoff and retry now
  // (a no-op while a stream is open).
  const onOnline = () => {
    if (stopped || ended || source) return;
    clearTimeout(timer);
    retry = 0;
    kick();
  };
  window.addEventListener("online", onOnline);

  kick();

  return {
    /** Re-attach to a resource that ENDED and has more to say.
     *
     *  A conversation is one run resumed in place (api_conversations.message): the `end`
     *  event stops this tail, and the next user message wakes the SAME run at the SAME
     *  offset. Re-attaching is therefore a catch-up from `base` and a fresh stream — what
     *  kick() already does — never a new tail from offset 0, which would re-render the
     *  whole thread and re-fetch every attachment. A stopped tail stays stopped: stop() is
     *  the view's teardown and nothing outlives it. */
    resume() {
      if (stopped || !ended) return;
      ended = false;
      retry = 0;
      clearTimeout(timer);
      kick();
    },
    stop() {
      stopped = true;
      close();
      clearTimeout(timer);
      window.removeEventListener("online", onOnline);
    },
  };
}
