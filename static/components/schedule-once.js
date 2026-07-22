// Schedule-once card (routine page): arm a ONE-SHOT future run at a picked instant — the gap
// between cron (repeats forever) and Run now (immediate). Shows the armed one-shots with a
// Cancel button and the daemon's fire ledger. The server only RECORDS the request in the spool
// (rsched.schedule_once); the daemon's OneShotManager fires it once at fire_at then consumes it
// (never repeats — nothing to clean up). A routine holding `scheduling` arms one via the
// schedule_run action; both write the same spool. See Help → schedule-once.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, toast, when } from "/static/util.js";

export function scheduleOnceCard(slug, opts = {}) {
  const body = el("div", { class: "oneshot-body" });
  const host = el("div", { class: "panel" },
    el("div", { class: "muted small", style: "margin-bottom:8px" },
      "fire this routine ONCE at a chosen time, then never again — the gap between the ",
      "schedule (repeats) and Run now (immediate). The daemon fires it at the instant below ",
      "and removes it; nothing to clean up."),
    body);
  refresh();
  return host;

  async function refresh() {
    try { render(await api(`/api/routines/${slug}/schedule-once`)); }
    catch (err) { toast(err.message, 4000, { error: true }); }
  }

  function render(d) {
    const armed = d.armed || [];
    body.replaceChildren(
      armed.length
        ? el("div", {}, ...armed.map(row))
        : el("div", { class: "muted small" }, "no one-shot armed — this routine fires on schedule or manually only"),
      d.fires ? el("div", { class: "muted small mt" },
        `fired ${d.fires}× as a one-shot · last ${d.last_fired ? when(d.last_fired) : "never"}`) : "",
      opts.protected ? "" : armForm());
  }

  function row(o) {
    return el("div", { class: "oneshot-row", style: "padding:8px 0;border-bottom:1px solid var(--line)" },
      el("div", { class: "row spread", style: "margin-bottom:4px" },
        el("div", { class: "row", style: "gap:10px" },
          el("span", { class: "ref-tag" }, "one-shot"),
          el("span", { title: o.fire_at }, "fires ", when(o.fire_at)),
          el("span", { class: "muted small" }, o.id)),
        opts.protected ? "" : el("button", { class: "btn small danger", onclick: () => cancel(o) }, "cancel")),
      o.reason ? el("div", { class: "muted small" }, o.reason) : "",
      el("div", { class: "muted small" }, `armed by ${o.requested_by || "?"}`));
  }

  function armForm() {
    const at = el("input", { type: "datetime-local", class: "code oneshot-at" });
    const reason = el("input", { type: "text", placeholder: "reason (optional)",
      class: "oneshot-reason", style: "flex:1;min-width:180px" });
    const add = el("button", { class: "btn primary", onclick: () => arm(at, reason) }, "arm one-shot");
    return el("div", { class: "row mt", style: "gap:8px;flex-wrap:wrap;align-items:center" },
      at, reason, add);
  }

  async function arm(at, reason) {
    const local = at.value;
    if (!local) { toast("pick a date & time first", 3000, { error: true }); return; }
    // datetime-local is naive LOCAL time; send an absolute UTC ISO instant to the API.
    const iso = new Date(local).toISOString();
    try {
      await api(`/api/routines/${slug}/schedule-once`,
        { method: "POST", body: { fire_at: iso, reason: reason.value || "" } });
      toast("one-shot armed");
      refresh();
    } catch (err) { toast(err.message, 4000, { error: true }); }
  }

  async function cancel(o) {
    if (!(await confirmDialog(`Cancel the one-shot armed for ${new Date(o.fire_at).toLocaleString()}? It will not fire.`,
                              { confirmLabel: "cancel one-shot" }))) return;
    try {
      await api(`/api/routines/${slug}/schedule-once/${o.id}`, { method: "DELETE" });
      toast("one-shot cancelled");
      refresh();
    } catch (err) { toast(err.message, 4000, { error: true }); }
  }
}
