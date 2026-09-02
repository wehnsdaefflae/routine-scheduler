// Shared Settings plumbing, used by the per-section settings-*.js modules.
//
// `panelSection` is the frame every one of those sections had written out by hand: a .panel
// carrying a skeleton, appended to the view SYNCHRONOUSLY, then one GET whose failure paints the
// error into that same panel instead of rejecting and taking the whole page down with it. The
// synchronous append is load-bearing, not incidental — settings.js appends every section in its
// final DOM order first and only then awaits the returned fill promises together (parallel loads,
// fixed order, one anchor jump onto settled heights). An async function runs to its first `await`
// synchronously, so the panel is in the document before the caller ever holds the promise.
//
// `remoteTester` is the git-remote Test button - it surfaces reachability/auth errors (e.g. a
// private repo before `gh auth login`) instead of failing silently later. The source-repository
// section is its only consumer: the library repo has no settings surface at all, because the
// library-sync routine manages that repo exclusively.

import { api } from "/static/api.js";
import { el, skeleton } from "/static/util.js";

export async function panelSection(view, url, skel, render) {
  const box = el("div", { class: "panel" });
  box.append(skeleton(skel));
  view.append(box);
  // `render` is handed `reload` as its third argument, because every one of these sections
  // mutates the thing it lists — delete a secret, add a machine, connect an account — and then
  // has to show the result. Re-running the section's own GET is the whole of that, and passing
  // it down keeps the fetch spelled once instead of each control re-fetching by hand.
  async function reload() {
    let d;
    try { d = await api(url); }
    catch (err) { box.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    await render(box, d, reload);
  }
  await reload();
}

export function remoteTester(input) {
  const result = el("span", { class: "small mono" });
  const btn = el("button", { class: "btn small" }, "test");
  btn.onclick = async () => {
    const remote = input.value.trim();
    if (!remote) { result.style.color = ""; result.textContent = "enter a URL first"; return; }
    btn.disabled = true; result.style.color = ""; result.textContent = "testing…";
    try {
      const r = await api("/api/settings/test-remote", { method: "POST", body: { remote } });
      result.style.color = r.ok ? "var(--ok)" : "var(--err)";
      result.textContent = r.ok ? `✓ ${r.detail || "reachable"}` : `✗ ${r.error}`;
      result.title = r.detail || "";        // raw git error on hover
    } catch (err) { result.style.color = "var(--err)"; result.textContent = `✗ ${err.message}`; }
    btn.disabled = false;
  };
  return { btn, result };
}
