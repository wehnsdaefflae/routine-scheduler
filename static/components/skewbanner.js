// The checkout moved under the process (F696): /static is served LIVE from disk while every
// Python object stays at the build this process booted with, so a release landing mid-day puts
// NEW javascript in front of an OLD server — and pydantic drops an unknown form field SILENTLY.
//
// That is the operator's R2546: he set up everything on the conversation-creation page and
// "most of the settings were gone" once the conversation started. The server is the only party
// holding both stamps (src/rsched/web/assetskew.py) and publishes the verdict on /api/status as
// `build_skew` + `disk_build`; this is where a person finds out.
//
// THE REMEDY IS THE SERVER, NOT THE PAGE (F745, operator 2026-10-22: "Clicking the button
// doesn't do anything"). The stale half is the PROCESS: the files on disk are already the new
// ones, so `location.reload()` re-fetches exactly what the browser already has and the banner
// comes straight back. Only a daemon relaunch picks up the new Python. So the primary action
// POSTs the same restart the Settings page posts, and the banner reports what stands between
// the request and the exit — the scheduler keeps starting runs and leaves at the first quiet
// gap (`daemon/restart.RESTART_IDLE_S`), which on a busy instance can be a long wait. Saying
// "queued behind N active run(s)" is the difference between a dead button and a slow one.
//
// Reload stays, demoted and honest: it is the fix for the OPPOSITE skew (an old page against a
// newer server, after a restart the tab slept through) and it costs nothing to offer.
//
// NOT dismissible, unlike the first-launch meta notice: a dismissed warning about a console
// that discards what you type is the same defect again.

import { el, toast, toastError } from "/static/util.js";
import { api } from "/static/api.js";

export function renderSkewBanner(s) {
  const banner = document.getElementById("skew-banner");
  if (!banner) return;
  if (!s || !s.build_skew) { banner.hidden = true; banner.replaceChildren(); return; }

  const restart = el("button", { class: "btn small primary" },
                     s.restart_requested ? "restart already requested" : "restart the server");
  const reload = el("button", { class: "btn small" }, "reload the page");
  reload.onclick = () => location.reload();
  const note = el("span", { class: "small muted" }, s.restart_requested
    ? " — waiting for a quiet moment; the console goes down for a few seconds, then comes back."
    : "");

  restart.disabled = !!s.restart_requested;
  restart.onclick = async () => {
    restart.disabled = true;
    try {
      const r = await api("/api/settings/restart", { method: "POST" });
      restart.textContent = "restart requested";
      note.textContent = r.active_runs
        ? ` — queued behind ${r.active_runs} active run(s)${r.parked ? ", one parked on you" : ""};`
          + " it restarts at the first quiet moment, then this banner clears."
        : " — restarting momentarily; the console will come back on its own.";
      toast("restart requested");
    } catch (err) {
      restart.disabled = false;
      toastError(err, 5000);
    }
  };

  banner.replaceChildren(
    el("span", { class: "nb-text" },
      el("b", {}, "This page is newer than the server. "),
      `The console's files are at ${s.disk_build || "a newer build"}, the running server at `,
      `${s.build || "an older one"}. Until the SERVER restarts, a form may send fields it does `,
      "not know — and it drops them without saying so. Reloading cannot fix this one: the files "
      + "are already current, the process is not."),
    restart, reload, note);
  banner.hidden = false;
}
