// Items: the system-maintenance index — every self-audit finding (F), decision (D) and bug
// report (R) filed by any run, with its status, purpose, origin and when it was addressed.
// It absorbed the Audit page in 0.106.0: the report header, the reviewer-feedback composer
// and the changelog all live here now, and every F/D/R reference anywhere in the console
// lands on a card built by itemcard.js.
//
// The feedback loop is explicit and unchanged: everything submitted lands in the self-audit
// routine's inbox and is consumed by its next run — the "waiting for the next run" list shows
// exactly what is queued, editable and withdrawable until then. Decisions are still ANSWERED
// on the Decisions page (one inbox, meta-badged); here they are read-only cards.

import { api } from "/static/api.js";
import { md } from "/static/md.js";
import { setQuery } from "/static/router.js";
import { itemCard } from "/static/components/itemcard.js";
import { focusRef, linkifyRefs } from "/static/components/reflinks.js";
import { chip, el, emptyState, skeleton, tagChip, toast, when } from "/static/util.js";
import { forgetField } from "/static/formpersist.js";

const TYPES = [["finding", "findings"], ["decision", "decisions"], ["bug", "bug reports"]];
const STATUSES = ["open", "in_progress", "addressed", "settled", "dropped", "unknown"];

export async function render(view, query = {}) {
  const filters = { type: query.type || "", status: query.status || "",
                    routine: query.routine || "", search: query.search || "" };
  const syncURL = () => setQuery({ ...filters, focus: query.focus || "" });
  let searchTimer = null;

  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / maintenance"),
      el("h1", {}, "Items"),
      el("div", { class: "sub" },
        "findings, decisions and bug reports — what it is, where it came from, when it was addressed")),
    el("div", { class: "row" },
      el("button", { class: "btn small", onclick: () => load() }, "↻ refresh"))));

  const header = el("div", {});
  const filterBar = el("div", { class: "filterbar" });
  const body = el("div", {});
  body.append(skeleton());
  view.append(header, filterBar, body);

  // ---- feedback → the routine's inbox → consumed by the next (or current) run -----------
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
  async function withdrawFeedback(id) {
    await api(`/api/audit/feedback/${encodeURIComponent(id)}`, { method: "DELETE" });
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
      try { await withdrawFeedback(p.id); }
      catch (err) { toast(err.message, 4000, { error: true }); drop.disabled = false; }
    };
    const edit = p.kind ? el("button", { class: "btn small ghost" }, "edit") : null;
    if (edit) edit.onclick = () => {
      const ta = el("textarea", { rows: 2, style: "min-height:auto;flex:1" });
      ta.value = p.raw || "";
      const save = el("button", { class: "btn small primary" }, "save");
      save.onclick = async () => {
        if (!ta.value.trim() && !p.choice) return;   // a decision may stand on its choice alone
        save.disabled = true;
        try { await updateFeedback(p.id, { kind: p.kind, target: p.target, choice: p.choice, text: ta.value }, "updated"); }
        catch (err) { toast(err.message, 4000, { error: true }); save.disabled = false; }
      };
      const cancel = el("button", { class: "btn small ghost", onclick: () => load() }, "cancel");
      row.replaceChildren(chip("queued", "waiting_user"), ta, save, cancel, drop);
      ta.focus();
    };
    row.append(chip("queued", "waiting_user"), el("span", { class: "p-text" }, p.text),
      p.ts ? when(p.ts) : null, edit, drop);
    return row;
  }

  function pendingSection(pending) {
    if (!pending.length) return null;
    return el("div", {},
      el("h2", {}, `Waiting for the next run · ${pending.length}`),
      el("div", { class: "panel" },
        el("div", { class: "muted small", style: "margin-bottom:4px" },
          "feedback already in the routine's inbox — editable and withdrawable right here until a self-audit run consumes it (then it disappears from this list)"),
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

  function generalSection(routineSlug) {
    // data-persist gives the draft an explicit storage key; discard clears a stale draft.
    const box = el("textarea", { class: "code", "data-persist": "audit-note",
      placeholder: "e.g. “add structured logging to the daemon runner”, or a priority/direction — a free-text prompt for the next self-audit run to act on" });
    const discard = el("button", { class: "btn small mt", title: "clear this draft — nothing is sent" }, "discard draft");
    discard.onclick = () => { box.value = ""; forgetField(box); };
    const send = el("button", { class: "btn primary mt" }, "send to the next run");
    send.onclick = async () => {
      const text = box.value;
      if (!text.trim()) return;
      send.disabled = true;
      // Clear the draft BEFORE submit()'s reload re-mounts the box — otherwise formpersist
      // refills the fresh (empty) box from the not-yet-forgotten draft and it looks unsent.
      box.value = ""; forgetField(box);
      try { await submit({ kind: "general", text }, "prompt sent"); }
      catch (err) { box.value = text; toast(err.message, 4000, { error: true }); }
      finally { send.disabled = false; }
    };
    // Fires self-audit immediately; an unsent note is delivered first so it isn't lost —
    // the fresh run drains the inbox at boot and reads it.
    const runNow = el("button", { class: "btn mt" }, "▶ run self-audit now");
    runNow.onclick = async () => {
      runNow.disabled = send.disabled = true;
      const text = box.value;
      try {
        if (text.trim()) {
          box.value = "";
          forgetField(box);   // clear BEFORE submit()'s reload re-mounts the box (else it refills)
          await submit({ kind: "general", text }, "prompt sent");
        }
        const r = await api(`/api/routines/${routineSlug}/run`, { method: "POST" });
        toast("self-audit started");
        location.hash = `#/run/${r.run_id}`;
      } catch (err) { toast(err.message, 5000, { error: true }); }
      finally { runNow.disabled = send.disabled = false; }
    };
    return el("div", {}, el("h2", {}, "Note for the next run"),
      el("div", { class: "panel" },
        el("div", { class: "muted small", style: "margin-bottom:8px" },
          "a prompt the self-audit routine reads on its next run — code changes to make, priorities, or anything not tied to an item above"),
        box, el("div", { class: "row", style: "gap:8px" }, send, runNow, discard),
        el("div", { class: "flow-note" },
          el("span", {}, "submit"), el("span", { class: "arrow" }, "→"),
          el("span", {}, "routine inbox"), el("span", { class: "arrow" }, "→"),
          el("span", {}, "consumed at the start of the next self-audit run — or immediately via ▶"))));
  }

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
            `The last run (${data.last_run.state}) produced no report. Items known from the changelog and the bug stream are still listed below.`)
        : emptyState("◌", "Never ran",
            "The self-audit routine runs on its schedule (or hit ▶ run now on its page). Leave a prompt below for its first run."));
    }

    renderFilterBar(data);

    const pending = data.pending_feedback || [];
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
          onSave: item.type === "finding" ? async (text, q) => {
            const payload = { kind: "comment", target: item.id, text };
            try {
              if (q) await updateFeedback(q.id, payload, "comment updated");
              else await submit(payload, "comment sent");
            } catch (err) { toast(err.message, 4000, { error: true }); }
          } : null,
          onWithdraw: async (q, btn) => {
            try { await withdrawFeedback(q.id); }
            catch (err) { toast(err.message, 4000, { error: true }); if (btn) btn.disabled = false; }
          },
        }));
      }
    }

    const changelog = changelogSection(data.changelog);
    if (changelog) body.append(changelog);
    body.append(generalSection(data.routine));
    // every F63/D14/R7 mention in the report's prose becomes a link to its card above
    linkifyRefs(body);
    linkifyRefs(header);
  }

  await load();
  // arriving via a ref link (#/items?focus=F63): land on the named card and flash it
  if (query.focus) focusRef(String(query.focus));
}
