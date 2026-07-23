// Shared Settings plumbing: the git-remote Test button - surfaces reachability/auth
// errors (e.g. a private repo before `gh auth login`) instead of failing silently later.
// Used by the library-repository and source-repository sections.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

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
