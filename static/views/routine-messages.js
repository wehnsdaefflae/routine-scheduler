// The four message folders of one routine (D74): every text FOR or FROM this routine, on
// its own page. inbox — waiting for the next run; yours to write, edit and withdraw until
// a run drains it (engine-filed deliveries included: the inbox file IS the delivery
// vehicle). outbox — reports this routine addressed to a sibling that the recipient has
// NOT picked up yet; retractable, never editable — the ledger is append-only and a report
// is the RUN's utterance, so a correction is a new message YOU write to the target's inbox
// (docs/messages.md). read — what this routine's runs consumed, newest first. received —
// addressed reports the recipient's run picked up. The last two are history, read-only.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, tagChip, toast, when } from "/static/util.js";
import { forgetField } from "/static/formpersist.js";

const FOLDERS = [
  ["inbox", "waiting for the next run — drained at boot (or a live run's next turn boundary); write, edit or withdraw freely until then"],
  ["outbox", "reports this routine addressed to another routine, not yet picked up — retractable until the recipient's run drains them"],
  ["read", "already consumed by this routine's runs, newest first — history, not a work queue"],
  ["received", "reports this routine filed that the recipient's run has picked up"],
];

const EMPTY = {
  inbox: "nothing waiting — the next run boots with an empty inbox",
  outbox: "no pending hand-offs — every addressed report was picked up (or none was filed)",
  read: "no consumed messages yet — they appear here once a run drains its inbox",
  received: "nothing picked up yet — a delivered report moves here from the outbox",
};

const msgId = (m) => String(m.file || "").replace(/\.json$/, "");
const fromLabel = (m) => (/^web(-|$)/.test(m.from || "") ? "you" : m.from || "user");

export function mountMessages(host, slug) {
  const tabs = el("div", { class: "msg-tabs" });
  const hint = el("div", { class: "muted small", style: "margin:4px 0 2px" });
  const body = el("div", {});
  host.append(el("div", { class: "panel" }, tabs, hint, body));
  let data = { inbox: [], outbox: [], read: [], received: [] };
  let folder = "inbox";

  async function load() {
    try { data = await api(`/api/routines/${slug}/messages`); }
    catch (err) {
      body.replaceChildren(el("div", { class: "muted small" },
        `couldn't load the messages — ${err.message}`));
      return;
    }
    render();
  }

  function render() {
    tabs.replaceChildren(...FOLDERS.map(([name]) =>
      tagChip(`${name} · ${(data[name] || []).length}`, {
        active: folder === name,
        onClick: () => { folder = name; render(); },
      })));
    hint.textContent = FOLDERS.find(([name]) => name === folder)[1];
    const rows = data[folder] || [];
    body.replaceChildren();
    if (folder === "inbox") body.append(composer());
    if (!rows.length) {
      body.append(el("div", { class: "msg-empty" }, EMPTY[folder]));
      return;
    }
    const list = el("div", { class: "msg-list" });
    for (const m of rows) list.append(folder === "inbox" ? inboxCard(m) : reportish(m));
    body.append(list);
  }

  // ---- inbox: the one writable queue ---------------------------------------------------
  function composer() {
    const box = el("textarea", { class: "code", "data-persist": `nextrun-msg-${slug}`,
      placeholder: "a message the routine reads at the start of its next run — a priority, "
        + "a correction, something to do or check" });
    const send = el("button", { class: "btn primary mt" }, "queue for the next run");
    send.onclick = async () => {
      const text = box.value;
      if (!text.trim()) return;
      send.disabled = true;
      box.value = ""; forgetField(box);   // clear before load() re-mounts the box
      try {
        const r = await api(`/api/routines/${slug}/messages`, { method: "POST", body: { text } });
        toast(r.delivery === "mid-run"
          ? "queued — the RUNNING run picks it up at its next turn"
          : "queued — the next run reads it at boot");
        await load();
      } catch (err) { box.value = text; toast(err.message, 4000, { error: true }); }
      finally { send.disabled = false; }
    };
    return el("div", { class: "msg-composer" },
      box, el("div", { class: "row mt" }, send),
      el("div", { class: "flow-note" },
        el("span", {}, "queue"), el("span", { class: "arrow" }, "→"),
        el("span", {}, "inbox"), el("span", { class: "arrow" }, "→"),
        el("span", {}, "drained at the start of the next run")));
  }

  function inboxCard(m) {
    const card = el("div", { class: "msg-item inbox" });
    const text = el("div", { class: "msg-text" }, m.text || "");
    const edit = el("button", { class: "btn small ghost",
      title: "rewrite the message in place — same file, same queue position" }, "edit");
    edit.onclick = () => {
      const ta = el("textarea", { class: "code", rows: 3, style: "min-height:auto" });
      ta.value = m.text || "";
      const save = el("button", { class: "btn small primary" }, "save");
      save.onclick = async () => {
        if (!ta.value.trim()) return;
        save.disabled = true;
        try {
          await api(`/api/routines/${slug}/messages/${msgId(m)}`,
            { method: "PUT", body: { text: ta.value } });
          toast("updated — still queued for the next run");
          await load();
        } catch (err) { toast(err.message, 4000, { error: true }); save.disabled = false; }
      };
      const cancel = el("button", { class: "btn small ghost", onclick: () => render() }, "cancel");
      card.replaceChildren(head(), ta, el("div", { class: "row mt", style: "gap:6px" }, save, cancel));
      ta.focus();
    };
    const drop = el("button", { class: "btn small ghost",
      title: "remove it from the inbox — the run never sees it" }, "withdraw");
    drop.onclick = async () => {
      drop.disabled = true;
      try {
        await api(`/api/routines/${slug}/messages/${msgId(m)}`, { method: "DELETE" });
        toast("withdrawn — the run won't see it");
        await load();
      } catch (err) { toast(err.message, 4000, { error: true }); drop.disabled = false; }
    };
    const head = () => el("div", { class: "msg-head" },
      el("span", { class: "msg-src" }, fromLabel(m)),
      m.report ? el("a", { class: "ref-link", href: `#/messages?focus=${m.report}`,
        title: "the delivered report behind this message" }, m.report) : null,
      m.ts ? when(m.ts) : null,
      el("span", { class: "msg-ops" }, edit, drop));
    card.append(head(), text);
    return card;
  }

  // ---- outbox / read / received: ledger rows and consumed history -----------------------
  function reportish(m) {
    const card = el("div", { class: `msg-item ${folder}` });
    const head = el("div", { class: "msg-head" });
    if (folder === "read") {
      head.append(el("span", { class: "msg-src" }, fromLabel(m)),
        m.report ? el("a", { class: "ref-link", href: `#/messages?focus=${m.report}` }, m.report) : null,
        m.ts ? when(m.ts) : null,
        m.run_ts ? el("a", { href: `#/run/${slug}:${m.run_ts}`,
          title: "the run that consumed it" }, "consumed by run ↗") : null);
      card.append(head, el("div", { class: "msg-text" }, m.text || ""));
      return card;
    }
    // outbox + received: an addressed report row (title + detail, ledger-derived)
    head.append(el("span", { class: "msg-src" }, `→ ${m.to || "?"}`),
      m.report ? el("a", { class: "ref-link", href: `#/messages?focus=${m.report}` }, m.report) : null,
      m.ts ? when(m.ts) : null);
    if (folder === "outbox") {
      const retract = el("button", { class: "btn small ghost",
        title: "withdraw the pending delivery — the recipient never sees it; the ledger "
          + "records the retraction and the item reads dropped" }, "retract");
      retract.onclick = async () => {
        if (!(await confirmDialog(
          `Retract ${m.report} before ${m.to} picks it up? To send a correction instead, `
          + "write your own message into that routine's inbox.",
          { confirmLabel: "retract", danger: true }))) return;
        retract.disabled = true;
        try {
          await api(`/api/routines/${slug}/outbox/${m.report}`, { method: "DELETE" });
          toast(`${m.report} retracted — ${m.to} never sees it`);
          await load();
        } catch (err) { toast(err.message, 5000, { error: true }); retract.disabled = false; }
      };
      head.append(el("span", { class: "msg-ops" }, retract));
    } else if (m.delivered) {
      head.append(el("a", { href: `#/run/${m.delivered.run_id}`,
        title: "the recipient's run that picked it up" }, "picked up ↗"),
        m.delivered.ts ? when(m.delivered.ts) : null);
    }
    card.append(head,
      m.title ? el("div", { class: "msg-title" }, m.title) : null,
      m.text ? el("div", { class: "msg-detail" }, m.text) : null);
    return card;
  }

  load();
  return { reload: load };
}
