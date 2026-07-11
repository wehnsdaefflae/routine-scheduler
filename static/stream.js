// Resilient transcript tail: REST catch-up for an authoritative byte offset, then an SSE
// tail from that offset. When the stream dies, we back off, catch up over REST (skipping
// events the dead stream already delivered), and reopen the SSE at the new offset — nothing
// is lost or duplicated. This generalizes the log view's "poll as fallback" pattern for the
// run view and the wizard chat.

import { api, sse } from "/static/api.js";

const MAX_BACKOFF_MS = 15000;

// page(offset)   → REST path returning {events, offset}
// events(offset) → SSE path emitting `transcript` / `state` / `end`
// onStatus(s)    → "live" | "reconnecting" | "ended"
// onGone()       → the resource 404'd (session archived / run pruned): stop for good
export function liveTail({ page, events, offset = 0, onEvent, onState, onStatus, onEnd, onGone }) {
  let base = offset;       // last byte offset confirmed by a REST page
  let seen = 0;            // events delivered by SSE since `base` (skip on catch-up)
  let source = null, timer = null, retry = 0, stopped = false, ended = false;

  const status = (s) => { if (!stopped && onStatus) onStatus(s); };
  const close = () => { if (source) { try { source.close(); } catch { /* already closed */ } source = null; } };

  async function catchUp() {
    const { events: evs, offset: next } = await api(page(base));
    for (const ev of evs.slice(seen)) onEvent(ev);
    base = next;
    seen = 0;
  }

  function open() {
    if (stopped || ended) return;
    source = sse(events(base), {
      transcript: (ev) => { retry = 0; seen += 1; onEvent(ev); },
      state: (s) => { retry = 0; if (onState) onState(s); },
      end: () => { ended = true; close(); status("ended"); if (onEnd) onEnd(); },
      onopen: () => status("live"),
      onerror: () => { if (stopped || ended) return; close(); reconnect(); },
    });
  }

  function reconnect() {
    status("reconnecting");
    if (retry === 0) {
      // first drop only — backoff retries of the same outage aren't new friction evidence
      import("/static/trace.js").then(({ trace }) => trace("reconnect", events)).catch(() => {});
    }
    const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** retry);
    retry += 1;
    timer = setTimeout(async () => {
      if (stopped || ended) return;
      try { await catchUp(); } catch (err) {
        if (err.status === 404) { stopped = true; if (onGone) onGone(); return; }
        reconnect();
        return;
      }
      open();
    }, delay);
  }

  (async () => {
    try { await catchUp(); } catch (err) {
      if (err.status === 404) { stopped = true; if (onGone) onGone(); return; }
      reconnect();
      return;
    }
    open();
  })();

  return {
    stop() { stopped = true; close(); clearTimeout(timer); },
  };
}
