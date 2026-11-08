// ONE reader of the engine's default operation limits for the whole console.
//
// `components/actiontime.js` compares a call's elapsed time with the limit the engine would
// apply to it, and used to carry its own copy of the numbers ({util: 300, script: 300,
// shell: 120, wait: 600}). A copy with nothing tying it to the engine goes quietly wrong:
// raising `shell`'s default in shellrun.py left every console in the fleet drawing its bar
// against 120 s, with no test anywhere to notice (D252). The server publishes the constants
// themselves on /api/status as `action_limits` (web/actionlimits.py) and this reads them.
//
// UNKNOWN is a state, not a zero: until the read lands nothing is claimed about a deadline,
// because a made-up limit and a made-up "no deadline" are both wrong. Callers render what
// they can and `onLimits` redraws them when the answer arrives — and a kind the server does
// NOT name has no operation limit at all, which is a fact once the map is known.
//
// Read once per page load, lazily on first use: the limits are process constants, so polling
// them would be a request per tab per interval for a value that cannot change under a live
// process, and a page with no transcript on it makes no request at all.
import { api } from "/static/api.js";

let limits = null;                        // the served map, or null while unknown
let started = false;
const waiting = new Set();

function load() {
  started = true;
  api("/api/status").then((s) => {
    limits = s?.action_limits && typeof s.action_limits === "object" ? s.action_limits : {};
    for (const cb of [...waiting]) cb();
    waiting.clear();
  }).catch(() => {
    // The daemon lamp already reports connectivity; a failed read leaves the limits unknown
    // rather than substituting numbers, and the next page load asks again.
    started = false;
  });
}

/** The seconds this action is measured against — null when there is no limit OR none is known
 *  yet (`known()` tells those apart). An action's own `timeout_s` wins: the served map is the
 *  default for one that named none. */
export function limitFor(action) {
  if (!started) load();
  // A discovery call (`util list|show|search`) runs the catalog reader, not the util: it has
  // no meaningful operation limit and never had one here.
  if (action.kind === "util" && ["list", "show", "search"].includes(action.name)) return null;
  const fallback = limits?.[action.kind];
  if (!fallback) return null;
  const own = Number(action.timeout_s);
  return Number.isFinite(own) && own > 0 ? own : Number(fallback);
}

/** Have the served limits landed? False means "nothing is known about any deadline yet". */
export function known() {
  if (!started) load();
  return limits !== null;
}

/** Call `cb` once, when the limits land (immediately if they already have). */
export function onLimits(cb) {
  if (known()) cb();
  else waiting.add(cb);
}
