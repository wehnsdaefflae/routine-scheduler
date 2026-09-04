// Messages: the system-maintenance index (the Items page renamed, D74) — every self-audit
// finding (F) and decision (D), and every report (R) a run filed (addressed to the routine
// that owns it, or left for triage), with its status, purpose, origin and when it was
// addressed. It absorbed the Audit page in 0.106.0: the report header, the reviewer-feedback
// composer and the changelog all live here, and every F/D/R reference anywhere in the
// console lands on a card built by itemcard.js.
//
// The feedback loop is explicit: everything submitted lands in the self-audit routine's
// inbox and is consumed by its next run — the "waiting for the next run" list shows the
// WHOLE queue (structured feedback, plain notes, engine deliveries), editable and
// withdrawable until then. A free note is a plain user message since D74 (created through
// the same generic endpoint every routine page uses); only the structured feedback kinds
// (finding comments, decision answers) keep their tagged channel, because their text is
// re-formatted from fields. Decisions are still ANSWERED on the Decisions page (one inbox,
// meta-badged); here they are read-only cards. Per-routine message folders live on each
// routine's own page (routine-messages.js).

import { api } from "/static/api.js";
import { md } from "/static/md.js";
import { setQuery } from "/static/router.js";
import { itemCard } from "/static/components/itemcard.js";
import { focusRef, linkifyRefs } from "/static/components/reflinks.js";
import { chip, el, emptyState, skeleton, tagChip, toast, when } from "/static/util.js";

const TYPES = [["finding", "findings"], ["decision", "decisions"], ["report", "reports"]];
const STATUSES = ["open", "in_progress", "addressed", "settled", "dropped", "unknown"];

export async function render(view, query = {}) {
  // Default to the ACTIVE backlog (open + in_progress) — the page is a worklist first,
  // an archive on request: `?status=all` (or any explicit status) overrides (D75).
  // A ?focus=<id> deep-link exists to show THAT card, which may be archived — so it
  // defaults to the whole set, not the active slice.
  const defaultStatus = query.focus ? "" : "open,in_progress";
  const filters = { type: query.type || "",
                    status: query.status ? (query.status === "all" ? "" : query.status)
                                         : defaultStatus,
                    routine: query.routine || "", search: query.search || "" };
  // An emptied status filter must survive reload as a CHOICE, not fall back to the
  // default — so "" (show everything) is written to the URL as the explicit "all".
  const syncURL = () => setQuery({ ...filters, status: filters.status || "all",
                                   focus: query.focus || "" });
  let searchTimer = null;

  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("h1", {}, "Messages"),
      el("div", { class: "sub" },
        "findings, decisions and reports — what it is, where it came from, when it was addressed")),
    el("div", { class: "row" },
      el("button", { class: "btn small", onclick: () => load() }, "↻ refresh"))));

  const header = el("div", {});
  const filterBar = el("div", { class: "filterbar" });
  const body = el("div", {});
  body.append(skeleton());
  // The two ways work leaves this ledger without becoming an open item anywhere. Above the list
  // because they are invisible to every filter below it: a deferred piece never became an item
  // at all (that is how D98's stopping panel was lost for six days), and an undelivered report
  // is addressed to a routine that has never heard of it (readmodels/orphans.py).
  const orphanBox = el("div", { hidden: true });
  view.append(header, orphanBox, filterBar, body);
  const orphanGroup = (title, rows, card) => {
    if (!rows.length) return null;
    return el("div", {},
      el("div", { class: "q-group-head" },
        el("span", {}, title),
        el("span", { class: "q-group-count" }, String(rows.length))),
      ...rows.map(card));
  };
  async function loadOrphans() {
    let rows;
    try { rows = await api("/api/items/orphans"); } catch { return; }
    if (!rows?.length) { orphanBox.hidden = true; orphanBox.replaceChildren(); return; }
    const deferrals = rows.filter((o) => o.kind !== "undelivered");
    const undelivered = rows.filter((o) => o.kind === "undelivered");
    orphanBox.hidden = false;
    // An undelivered orphan can never be delivered (no run has a message to drain), so the only
    // action is to clear it: discard marks it dropped, off this banner and out of the backlog.
    const discard = (o) => {
      const b = el("button", { class: "btn small ghost",
        title: "discard this orphan — marks it dropped, off the banner and out of the backlog" },
        "discard");
      b.onclick = async () => {
        b.disabled = true;
        try {
          await api(`/api/items/orphans/${encodeURIComponent(o.id)}/discard`, { method: "POST" });
          toast(`${o.id} discarded — dropped from the backlog`);
          await loadOrphans();
        } catch (err) { toast(err.message, 4000, { error: true }); b.disabled = false; }
      };
      return b;
    };
    orphanBox.replaceChildren(...[
      orphanGroup("deferred, then lost — a carrier item closed without delivering these",
        deferrals, (o) => el("div", { class: "card mt" },
          el("div", {}, el("strong", {}, o.source_ids.join(", ")),
            " was deferred into ", el("a", { href: `#/messages?focus=${o.carrier}` }, o.carrier),
            `, which closed ${o.carrier_status} without naming it.`),
          el("div", { class: "faint small mt" }, o.promise))),
      orphanGroup("addressed, never delivered — the target has no message for these and never will",
        undelivered, (o) => el("div", { class: "card mt" },
          el("div", { class: "row",
            style: "justify-content:space-between;gap:8px;align-items:flex-start" },
            el("div", {}, el("strong", {}, o.id), " from ", el("span", { class: "mono" }, o.from),
              " is addressed to ", el("span", { class: "mono" }, o.target),
              o.target_exists ? ", whose inbox holds no message for it."
                              : ", which is not a routine on this instance."),
            discard(o)),
          el("div", { class: "prose mt" }, o.title))),
    ].filter(Boolean));
  }
  loadOrphans();

  // ---- feedback → the routine's inbox → consumed by the next (or current) run -----------
  // Structured feedback (finding comments, decision answers) keeps the tagged audit
  // channel — its text is re-formatted from fields. Plain messages (the free note, or any
  // other queued message) go through the generic per-routine message endpoints (D74).
  let routineSlug = "self-audit";   // overwritten from the API response on every load
  async function submit(payload, okMsg) {
    const r = await api("/api/audit/feedback", { method: "POST", body: payload });
    toast(r.delivery === "mid-run"
      ? `${okMsg} → inbox → the RUNNING self-audit picks it up this run`
      : `${okMsg} → inbox → consumed by the next self-audit run`, 4200);
    await load();
  }
  async function updateFeedback(id, payload, okMsg) {
    await api(`/api/audit/feedback/${encodeURIComponent(id)}`, { method: "PUT", body: payload });
    toast(`${okMsg} — still queued for the next run`);
    await load();
  }
  async function updateMessage(id, text) {
    await api(`/api/routines/${routineSlug}/messages/${encodeURIComponent(id)}`,
      { method: "PUT", body: { text } });
    toast("updated — still queued for the next run");
    await load();
  }
  async function withdrawMessage(id) {
    await api(`/api/routines/${routineSlug}/messages/${encodeURIComponent(id)}`,
      { method: "DELETE" });
    toast("withdrawn — the run won't see it");
    await load();
  }

  // ---- sections ------------------------------------------------------------------------
  function pendingRow(p) {
    const row = el("div", { class: "pending-item" });
    const drop = el("button", { class: "btn small ghost",
      title: "remove from the inbox — the run never sees it" }, "withdraw");
    drop.onclick = async () => {
      drop.disabled = true;
      try { await withdrawMessage(p.id); }
      catch (err) { toast(err.message, 4000, { error: true }); drop.disabled = false; }
    };
    const edit = el("button", { class: "btn small ghost" }, "edit");
    edit.onclick = () => {
      const ta = el("textarea", { rows: 2, style: "min-height:auto;flex:1" });
      ta.value = p.kind ? (p.raw || "") : (p.text || "");
      const save = el("button", { class: "btn small primary" }, "save");
      save.onclick = async () => {
        if (!ta.value.trim() && !p.choice) return;   // a decision may stand on its choice alone
        save.disabled = true;
        try {
          if (p.kind) await updateFeedback(p.id, { kind: p.kind, target: p.target, choice: p.choice, text: ta.value }, "updated");
          else await updateMessage(p.id, ta.value);
        } catch (err) { toast(err.message, 4000, { error: true }); save.disabled = false; }
      };
      const cancel = el("button", { class: "btn small ghost", onclick: () => load() }, "cancel");
      row.replaceChildren(chip("queued", "waiting_user"), ta, save, cancel, drop);
      ta.focus();
    };
    // filter(Boolean): append stringifies a null argument into the text "null" (el() drops
    // null children, append does not) — a queued item with no ts rendered a literal "null".
    row.append(...[chip("queued", "waiting_user"), el("span", { class: "p-text" }, p.text),
      p.ts ? when(p.ts) : null, edit, drop].filter(Boolean));
    return row;
  }

  function pendingSection(pending) {
    if (!pending.length) return null;
    return el("div", {},
      el("h2", {}, `Waiting for the next run · ${pending.length}`),
      el("div", { class: "panel" },
        el("div", { class: "muted small", style: "margin-bottom:4px" },
          "the self-audit routine's whole inbox — every queued message stays editable and withdrawable right here until a run consumes it (then it disappears from this list)"),
        ...pending.map(pendingRow)));
  }

  // The full changelog, including rows that name no item — an item's own history rides on its
  // card (`addressed`), but the archive as a whole stays readable here.
  function changelogSection(entries) {
    if (!(entries || []).length) return null;
    return el("details", { class: "mt" },
      el("summary", {}, el("strong", {}, `Changelog · ${entries.length} recorded changes`)),
      ...entries.map((c) => el("div", { class: "panel mt" },
        el("div", { class: "row spread" },
          el("strong", { class: "prose" }, c.summary || c.title || "(change)"),
          el("span", { class: "muted small" },
            c.ts ? when(c.ts) : null,
            c.commit ? ` · ${String(c.commit).slice(0, 8)}` : "")),
        c.detail ? md(c.detail, "md muted mt prose") : null)));
  }

  // "Message the next run" retired from this page (user order 2026-08-12): it duplicated
  // the generic Messages channel on self-audit's own routine page — write there instead.

  // ---- filters -------------------------------------------------------------------------
  // The select and the search box are built ONCE and re-appended on every render: rebuilding
  // them would drop the caret mid-keystroke (the search reloads on a 250ms debounce).
  const routineSel = el("select", { style: "margin-left:10px" });
  routineSel.onchange = () => { filters.routine = routineSel.value; syncURL(); load(); };
  const searchIn = el("input", { type: "search", class: "search", value: filters.search,
    placeholder: "search id · title · detail…", style: "margin-left:6px" });
  searchIn.oninput = () => {
    filters.search = searchIn.value.trim();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { syncURL(); load(); }, 250);
  };

  function renderFilterBar(data) {
    const counts = data.counts || { type: {}, status: {} };
    const routines = [...new Set((data.items || []).map((i) => i.origin?.routine).filter(Boolean))].sort();
    const pick = (key, value) => {
      filters[key] = filters[key] === value ? "" : value;
      syncURL(); load();
    };
    filterBar.replaceChildren(el("span", { class: "lbl" }, "type"));
    for (const [value, label] of TYPES)
      filterBar.append(tagChip(`${label} ${counts.type[value] || 0}`,
        { active: filters.type === value, onClick: () => pick("type", value) }));
    filterBar.append(el("span", { class: "lbl", style: "margin-left:10px" }, "status"));
    const ACTIVE = "open,in_progress";
    const nActive = (counts.status.open || 0) + (counts.status.in_progress || 0);
    filterBar.append(tagChip(`active ${nActive}`,
      { active: filters.status === ACTIVE, onClick: () => pick("status", ACTIVE) }));
    for (const s of STATUSES)
      if (counts.status[s])
        filterBar.append(tagChip(`${s} ${counts.status[s]}`,
          { active: filters.status === s, onClick: () => pick("status", s) }));
    routineSel.replaceChildren(el("option", { value: "" }, "All routines"),
      ...routines.map((r) => el("option", { value: r }, r)));
    routineSel.value = filters.routine;
    filterBar.append(routineSel, searchIn);
    if (filters.type || filters.status || filters.routine || filters.search)
      filterBar.append(el("button", { class: "btn ghost small", onclick: () => {
        Object.assign(filters, { type: "", status: "", routine: "", search: "" });
        searchIn.value = ""; syncURL(); load();
      } }, "clear"));
  }

  // ---- load ----------------------------------------------------------------------------
  async function load() {
    const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
    let data;
    try { data = await api(`/api/items?${qs}`); }
    catch (err) { body.replaceChildren(emptyState("✕", "Couldn't load the items", err.message)); return; }

    header.replaceChildren();
    body.replaceChildren();
    if (!data.exists) {
      body.append(emptyState("◌", "The self-audit routine isn't set up yet",
        "Once it's created (it ships with the install, under the meta tag) and has run, its findings and decisions appear here — together with any bug report a run files."));
      return;
    }

    // report header: the current window, the summary, the last run
    const r = data.report;
    if (r) {
      const meta = el("div", { class: "muted small", style: "margin-bottom:4px" });
      if (r.since?.window) meta.append(`${r.since.window}  ·  `);
      if (r.generated) meta.append("generated ", when(r.generated));
      if (r.since?.commit) meta.append(`  ·  since ${String(r.since.commit).slice(0, 8)}`);
      header.append(meta);
      if (r.summary) header.append(md(r.summary, "md panel prose"));
    } else {
      header.append(data.last_run
        ? emptyState("▢", "No report from the last run",
            `The last run (${data.last_run.state}) produced no report. Items known from the changelog and the report ledger are still listed below.`)
        : emptyState("◌", "Never ran",
            "The self-audit routine runs on its schedule (or hit ▶ run now on its page). Leave a prompt below for its first run."));
    }

    renderFilterBar(data);

    routineSlug = data.routine || routineSlug;
    const pending = data.queued || [];
    // which decision ids already have an answer queued, and which findings a comment —
    // structured fields when present, text parse for messages queued before they existed
    const queuedDecisions = new Map();
    const queuedComments = new Map();   // finding id → latest queued comment (oldest first, last wins)
    for (const p of pending) {
      if (p.kind === "decision" && p.target) { queuedDecisions.set(p.target, p); continue; }
      if (p.kind === "comment" && p.target) { queuedComments.set(p.target, p); continue; }
      const m = /^\[AUDIT decision · ([^\]]+)\]\s*(.*)$/.exec(p.text || "");
      if (m) queuedDecisions.set(m[1].trim(), p);
    }
    const answered = new Set(data.answered_decisions || []);

    const pendingBox = pendingSection(pending);
    if (pendingBox) body.append(pendingBox);

    const items = data.items || [];
    const total = data.total ?? items.length;
    body.append(el("h2", {}, `Items · ${items.length}${total > items.length ? ` of ${total}` : ""}`));
    if (!items.length) {
      body.append(emptyState("▢", "Nothing matches these filters",
        "Clear the type / status / routine filters above."));
    } else {
      for (const item of items) {
        const queued = item.type === "decision" ? queuedDecisions.get(item.id)
          : queuedComments.get(item.id);
        body.append(itemCard(item, {
          queued,
          answered: answered.has(item.id),
          onPriority: async (on) => {
            try {
              await api(`/api/items/${item.id}/priority`, { method: "POST", body: { on } });
              toast(on ? `${item.id} flagged ⚑ — floats here, and its owner's next run reads it first`
                       : `${item.id} unflagged`);
              await load();
            } catch (err) { toast(err.message, 4000, { error: true }); }
          },
          onSave: item.type === "finding" ? async (text, q) => {
            const payload = { kind: "comment", target: item.id, text };
            try {
              if (q) await updateFeedback(q.id, payload, "comment updated");
              else await submit(payload, "comment sent");
            } catch (err) { toast(err.message, 4000, { error: true }); }
          } : null,
          onWithdraw: async (q, btn) => {
            try { await withdrawMessage(q.id); }
            catch (err) { toast(err.message, 4000, { error: true }); if (btn) btn.disabled = false; }
          },
        }));
      }
    }

    const changelog = changelogSection(data.changelog);
    if (changelog) body.append(changelog);
    // every F63/D14/R7 mention in the report's prose becomes a link to its card above
    linkifyRefs(body);
    linkifyRefs(header);
  }

  await load();
  // arriving via a ref link (#/messages?focus=F63): land on the named card and flash it
  if (query.focus) focusRef(String(query.focus));
}
