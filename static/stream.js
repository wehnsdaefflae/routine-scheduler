// Resilient transcript tail: REST catch-up for an authoritative byte offset, then an SSE
// tail from that offset. When the stream dies, we back off (with jitter — correlated drops
// must not reconnect in lockstep), catch up over REST (skipping events the dead stream
// already delivered), and reopen the SSE at the new offset — nothing is lost or duplicated.
// This generalizes the log view's "poll as fallback" pattern for the run view and the chat.
//
// F263 (the multi-run "freeze" is a NETWORK stall): browsers cap ~6 HTTP/1.1 connections
// per origin, and every EventSource holds one for its whole life — enough live tails (run
// page + expanded activity rows + a conversation + the global bus) exhaust the pool, and
// then every fetch on the page stalls with no error and no long task. So tails hold at most
// MAX_TAIL_STREAMS sockets between them (+1 for the bus = headroom stays for fetches, and a
// stalled stream can never wedge the page); a tail that cannot get a slot falls back to
// REST polling — same events, ~POLL_MS latency, socket released after every page — and
// upgrades itself to SSE as soon as a slot frees.

import { api, openStreamCount, sse } from "/static/api.js";

const MAX_BACKOFF_MS = 15000;
export const MAX_TAIL_STREAMS = 3;   // held tail sockets; + the global bus ≤ 4 of ~6/origin
const POLL_MS = 3000;                // REST-fallback cadence while no SSE slot is free

let tailStreams = 0;                 // module-wide census of held tail EventSources

// page(offset)   → REST path returning {events, offset}
// events(offset) → SSE path emitting `transcript` / `state` / `end`
// onStatus(s)    → "live" | "reconnecting" | "ended"
// onGone()       → the resource 404'd (session archived / run pruned): stop for good
export function liveTail({ page, events, offset = 0, onEvent, onState, onStatus, onEnd, onGone }) {
  let base = offset;       // last byte offset confirmed by a REST page
  let seen = 0;            // events delivered by SSE since `base` (skip on catch-up)
  let source = null, timer = null, retry = 0, stopped = false, ended = false;
  let held = false;        // this tail currently holds one of the MAX_TAIL_STREAMS slots
  let openedAt = 0, seenSinceOpen = 0;   // F175: how long each stream lived + what it carried

  const status = (s) => { if (!stopped && onStatus) onStatus(s); };
  const release = () => { if (held) { held = false; tailStreams -= 1; } };
  const close = () => {
    if (source) { try { source.close(); } catch { /* already closed */ } source = null; }
    release();
  };

  async function catchUp() {
    const { events: evs, offset: next } = await api(page(base));
    for (const ev of evs.slice(seen)) onEvent(ev);
    base = next;
    seen = 0;
  }

  function open() {
    if (stopped || ended) return;
    if (tailStreams >= MAX_TAIL_STREAMS) { poll(); return; }   // no slot — poll until one frees
    held = true; tailStreams += 1;
    source = sse(events(base), {
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

  // REST fallback while every tail slot is held: deliver events by polling, report "live"
  // (data flows, just at POLL_MS latency), and retry open() each round — it upgrades to SSE
  // the moment a slot frees. Deliberately no end-of-run detection here: a resumed
  // conversation appends after its finish event, so ending the tail on one would drop the
  // live leg; the poll is one small GET per round and the view's unmount stops it.
  function poll() {
    status("live");
    timer = setTimeout(async () => {
      if (stopped || ended) return;
      try { await catchUp(); } catch (err) {
        if (err.status === 404) { stopped = true; if (onGone) onGone(); return; }
      }
      open();
    }, POLL_MS);
  }

  function reconnect(streamsAtDrop = openStreamCount()) {
    status("reconnecting");
    if (retry === 0) {
      // first drop only — backoff retries of the same outage aren't new friction evidence.
      // The detail records the stream's age and traffic (F175: run-view streams die every
      // ~2min — age/traffic tells an idle-timeout kill from a mid-burst one).
      // F263: stamp the concurrent open-EventSource count — a reconnect burst under a high
      // stream count is the connection-exhaustion signature behind the network-stall freeze.
      const detail = (openedAt
        ? `alive ${Math.round((Date.now() - openedAt) / 1000)}s, ${seenSinceOpen} events`
        : "before first open") + `, ${streamsAtDrop} streams open`;
      import("/static/trace.js").then(({ trace }) => trace("reconnect", events(base), detail)).catch(() => {});
    }
    // jitter (±25%): tails killed together (proxy restart, network blip) must not thunder
    // back in lockstep and re-exhaust the connection pool they just freed
    const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** retry) * (0.75 + Math.random() * 0.5);
    retry += 1;
    timer = setTimeout(kick, delay);
  }

  async function kick() {
    if (stopped || ended) return;
    try { await catchUp(); } catch (err) {
      if (err.status === 404) { stopped = true; if (onGone) onGone(); return; }
      reconnect();
      return;
    }
    open();
  }

  // The browser knows when connectivity returns — skip the remaining backoff and retry now
  // (a no-op while a stream is open or a poll round is already scheduled to fire soon).
  const onOnline = () => {
    if (stopped || ended || source) return;
    clearTimeout(timer);
    retry = 0;
    kick();
  };
  window.addEventListener("online", onOnline);

  kick();

  return {
    stop() {
      stopped = true;
      close();
      clearTimeout(timer);
      window.removeEventListener("online", onOnline);
    },
  };
}
