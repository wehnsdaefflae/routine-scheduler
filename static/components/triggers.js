// Triggers card (routine page): event-driven fires alongside cron. Create/delete webhook
// triggers (copy the hook URL) and REPORT triggers (fire when a report/inbox message
// lands for this routine — the daemon watches the inbox, bursts coalesce into one run per
// cooldown window). The server generates id + token; the URL's token IS the hook's auth,
// so the card treats it like a secret worth copying, never re-asking. See Help → triggers.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, toast, when } from "/static/util.js";

export function triggersCard(slug, initial, opts = {}) {
  const body = el("div", { class: "triggers-body" });
  const host = el("div", { class: "panel" },
    el("div", { class: "muted small", style: "margin-bottom:8px" },
      "fire this routine on an external EVENT, alongside the schedule: POST anything to a ",
      "trigger's hook URL and the daemon queues one run — bursts and hits during an active ",
      "run coalesce into a single fire whose run receives every payload as a message. ",
      "The URL's token is the only auth on the hook: share it like a secret."),
    body);
  render(initial || []);
  return host;

  async function refresh() {
    try { const d = await api(`/api/routines/${slug}`); render(d.triggers || []); }
    catch (err) { toast(err.message, 4000, { error: true }); }
  }

  function render(rows) {
    const cooldownIn = el("input", { type: "number", min: "0", value: "60", style: "width:80px",
      title: "minimum seconds between trigger-initiated fires — more events coalesce into one" });
    const hasReport = rows.some((t) => t.type === "report");
    body.replaceChildren(
      rows.length ? el("div", {}, ...rows.map(row))
        : el("div", { class: "muted small" }, "no triggers yet — this routine fires on schedule or manually only"),
      opts.protected ? "" : el("div", { class: "row mt", style: "gap:8px;flex-wrap:wrap" },
        el("span", { class: "muted small" }, "cooldown (s)"), cooldownIn,
        el("button", { class: "btn primary", onclick: () => create(cooldownIn) }, "+ add webhook trigger"),
        el("button", { class: "btn", ...(hasReport ? { disabled: "" } : {}),
          title: hasReport
            ? "one inbox, one watcher — this routine already has a report trigger"
            : "fire this routine when a report or message lands in its inbox (default cooldown 15 min — bursts become one run)",
          onclick: () => createReport() }, "+ add report trigger")));
  }

  function row(t) {
    const meta = el("div", { class: "row muted small", style: "gap:14px;flex-wrap:wrap" },
      el("span", {}, "last fired · ", t.last_fired ? when(t.last_fired) : "never"),
      el("span", {}, `fired events · ${t.events || 0}`),
      el("span", { title: "events recorded but not yet turned into a run (coalescing)" },
        `pending · ${t.pending || 0}`),
      el("span", { title: "minimum seconds between trigger-initiated fires — more events coalesce" },
        `cooldown · ${t.cooldown_s}s`));
    const urlLine = t.type === "webhook" ? webhookUrl(t)
      : t.type === "report"
        ? el("div", { class: "muted small" },
            "fires when a report or message lands in this routine's inbox — deliveries within the cooldown coalesce into one run")
        : el("div", { class: "muted small" }, "reserved trigger type — nothing fires it yet");
    return el("div", { class: "trigger-row", style: "padding:8px 0;border-bottom:1px solid var(--line)" },
      el("div", { class: "row spread", style: "margin-bottom:6px" },
        el("div", { class: "row", style: "gap:10px" },
          el("span", { class: "ref-tag" }, t.type),
          el("span", { class: "muted small" }, t.id)),
        opts.protected ? "" : el("button", { class: "btn small danger", onclick: () => remove(t) }, "delete")),
      urlLine, meta);
  }

  function webhookUrl(t) {
    const url = `${location.origin}${t.url_path}`;
    const input = el("input", { type: "text", readonly: true, value: url,
      class: "code", style: "flex:1;min-width:240px", onclick: (e) => e.target.select() });
    const copy = el("button", { class: "btn small", onclick: async () => {
      try { await navigator.clipboard.writeText(url); toast("hook URL copied"); }
      catch { input.select(); toast("clipboard blocked — URL selected, press Ctrl-C"); }
    } }, "copy");
    return el("div", { class: "row", style: "gap:8px;margin-bottom:6px" }, input, copy);
  }

  async function create(cooldownIn) {
    const cooldown = parseInt(cooldownIn?.value, 10);
    try {
      await api(`/api/routines/${slug}/triggers`, { method: "POST",
        body: { type: "webhook",
                ...(Number.isFinite(cooldown) && cooldown >= 0 ? { cooldown_s: cooldown } : {}) } });
      toast("webhook trigger created");
      refresh();
    } catch (err) { toast(err.message, 4000, { error: true }); }
  }

  async function createReport() {
    // no cooldown input: the server default (15 min) fits the burst-coalescing intent;
    // the row shows the value and the operator can delete/recreate to change it
    try {
      await api(`/api/routines/${slug}/triggers`, { method: "POST", body: { type: "report" } });
      toast("report trigger created — a delivered report now wakes this routine");
      refresh();
    } catch (err) { toast(err.message, 4000, { error: true }); }
  }

  async function remove(t) {
    if (!(await confirmDialog(`Delete trigger ${t.id}? Its hook URL stops working immediately.`,
                              { confirmLabel: "delete" }))) return;
    try {
      await api(`/api/routines/${slug}/triggers/${t.id}`, { method: "DELETE" });
      toast("trigger deleted");
      refresh();
    } catch (err) { toast(err.message, 4000, { error: true }); }
  }
}
