// The checkout moved under the process (F696): /static is served LIVE from disk while every
// Python object stays at the build this process booted with, so a release landing mid-day puts
// NEW javascript in front of an OLD server — and pydantic drops an unknown form field SILENTLY.
//
// That is the operator's R2546: he set up everything on the conversation-creation page and
// "most of the settings were gone" once the conversation started. The server is the only party
// holding both stamps (src/rsched/web/assetskew.py) and publishes the verdict on /api/status as
// `build_skew` + `disk_build`; this is where a person finds out.
//
// NOT dismissible, unlike the first-launch meta notice: reloading is the whole fix, and a
// dismissed warning about a console that discards what you type is the same defect again.

import { el } from "/static/util.js";

export function renderSkewBanner(s) {
  const banner = document.getElementById("skew-banner");
  if (!banner) return;
  if (!s || !s.build_skew) { banner.hidden = true; banner.replaceChildren(); return; }
  const reload = el("button", { class: "btn small primary" }, "reload the console");
  reload.onclick = () => location.reload();
  banner.replaceChildren(
    el("span", { class: "nb-text" },
      el("b", {}, "This page is newer than the server. "),
      `The console's files are at ${s.disk_build || "a newer build"}, the running server at `,
      `${s.build || "an older one"}. Until you reload, a form may send fields this server does `,
      "not know — and it drops them without saying so."),
    reload);
  banner.hidden = false;
}
