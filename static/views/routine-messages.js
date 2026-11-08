// The four message folders of one routine (D74): every text FOR or FROM this routine, on
// its own page. inbox — waiting for the next run; yours to write, edit and withdraw until
// a run drains it (engine-filed deliveries included: the inbox file IS the delivery
// vehicle), WITH FILE ATTACHMENTS (D275): the same picker and the same multipart endpoint a
// conversation message uses, and each attached file shown back on the message — a thumbnail
// for an image, an open chip otherwise — fetched through the authenticated attachment route. outbox — reports this routine addressed to a sibling that the recipient has
// NOT picked up yet; retractable, never editable — the ledger is append-only and a report
// is the RUN's utterance, so a correction is a new message YOU write to the target's inbox
// (docs/messages.md). read — what this routine's runs consumed, newest first. received —
// addressed reports the recipient's run picked up. The last two are history, read-only.

import { api, apiUpload } from "/static/api.js";
import { clampedBody } from "/static/md.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, tagChip, toast, toastError, when } from "/static/util.js";
import { filePicker } from "/static/components/filepicker.js";
import { forgetField } from "/static/formpersist.js";
import { attachmentRow, revokeBlobs } from "/static/components/obs-attachments.js";
import { refHref } from "/static/components/reflinks.js";

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
  const hint = el("div", { class: "set-desc muted small", style: "margin:4px 0 2px" });
  const body = el("div", {});
  host.append(el("div", { class: "panel" }, tabs, hint, body));
  let data = { inbox: [], outbox: [], read: [], received: [] };
  let folder = "inbox";
  // Every attachment thumbnail holds its decoded bytes until its object URL is revoked, and
  // this panel re-renders on every folder click and every run lifecycle event — so the
  // MOUNTER owns the array and revokes it (obs-attachments.js: "the component that mounted
  // the row revokes them in its destroy(), exactly once").
  const blobs = [];
  const fileUrl = (rel) =>
    `/api/routines/${slug}/attachment?path=${encodeURIComponent(rel)}`;

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
    revokeBlobs(blobs);          // the rows about to be replaced held these
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
    // D275: files ride along, exactly as they do on a conversation message and the run
    // page's injection — the same picker (chips, paste-to-attach for a dragged-in
    // screenshot) and the same multipart endpoint. A file WITHOUT words is a real message
    // (the API makes the `[attached files]` block the body), so the guard below admits it.
    const { picker, files, clearFiles, wirePaste } = filePicker();
    wirePaste(box);
    send.onclick = async () => {
      const text = box.value;
      const picked = files();
      if (!text.trim() && !picked.length) return;
      send.disabled = true;
      box.value = ""; forgetField(box);   // clear before load() re-mounts the box
      try {
        const fd = new FormData();
        fd.append("text", text);
        for (const f of picked) fd.append("files", f);
        const r = await apiUpload(`/api/routines/${slug}/messages`, fd);
        clearFiles();
        toast(r.delivery === "mid-run"
          ? "queued — the RUNNING run picks it up at its next turn"
          : "queued — the next run reads it at boot");
        await load();
      } catch (err) { box.value = text; toastError(err); }
      finally { send.disabled = false; }
    };
    return el("div", { class: "msg-composer" },
      box, el("div", { class: "row mt" }, send, picker),
      el("div", { class: "flow-note" },
        el("span", {}, "queue"), el("span", { class: "arrow" }, "→"),
        el("span", {}, "inbox"), el("span", { class: "arrow" }, "→"),
        el("span", {}, "drained at the start of the next run")));
  }

  function inboxCard(m) {
    const card = el("div", { class: "msg-item inbox" });
    // The same queue the Messages page shows, so the same treatment and the same code: a
    // delivered REPORT runs to 700px, and seven of them made a routine page's MESSAGES panel
    // 1 700px of the seven items you can already read in full one click away.
    const { node: text, toggle: more } = clampedBody(m.text, "msg-text");
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
          await api(`/api/routines/${slug}/messages/${encodeURIComponent(msgId(m))}`,
            { method: "PUT", body: { text: ta.value } });
          toast("updated — still queued for the next run");
          await load();
        } catch (err) { toastError(err); save.disabled = false; }
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
        await api(`/api/routines/${slug}/messages/${encodeURIComponent(msgId(m))}`, { method: "DELETE" });
        toast("withdrawn — the run won't see it");
        await load();
      } catch (err) { toastError(err); drop.disabled = false; }
    };
    const head = () => el("div", { class: "msg-head" },
      el("span", { class: "msg-src" }, fromLabel(m)),
      m.report ? el("a", { class: "ref-link", href: refHref(m.report),
        title: "the delivered report behind this message" }, m.report) : null,
      m.ts ? when(m.ts) : null,
      el("span", { class: "msg-ops" }, ...[more, edit, drop].filter(Boolean)));
    card.append(head(), text);
    // What the user attached (D275): images as thumbnails, everything else as an open chip,
    // both fetched through the authenticated attachment route and opened from a blob URL
    // via components/blobtab.js — the console's one rule for handing a fetched file a tab.
    attach(card, m);
    return card;
  }

  // el() drops a null child; the native append would stringify it into a literal "null"
  // text node (the warning below), so the row is appended only when there IS one.
  function attach(card, m) {
    const row = attachmentRow(m.attachments, fileUrl, blobs);
    if (row) card.append(row);
  }

  // ---- outbox / read / received: ledger rows and consumed history -----------------------
  // Children go THROUGH el() (it drops null/undefined) — the native DOM append would
  // stringify a ternary's null into a literal "null" text node.
  function reportish(m) {
    const card = el("div", { class: `msg-item ${folder}` });
    if (folder === "read") {
      // fifty consumed messages is history, and history is read by its heads — same clamp
      const { node: text, toggle: more } = clampedBody(m.text, "msg-text");
      card.append(el("div", { class: "msg-head" },
        el("span", { class: "msg-src" }, fromLabel(m)),
        m.report ? el("a", { class: "ref-link", href: refHref(m.report) }, m.report) : null,
        m.ts ? when(m.ts) : null,
        m.run_ts ? el("a", { href: `#/run/${slug}:${m.run_ts}`,
          title: "the run that consumed it" }, "consumed by run ↗") : null,
        more ? el("span", { class: "msg-ops" }, more) : null),
        text);
      attach(card, m);     // a consumed message keeps its rels; the files never moved
      return card;
    }
    // outbox + received: an addressed report row (title + detail, ledger-derived)
    const head = el("div", { class: "msg-head" },
      el("span", { class: "msg-src" }, `→ ${m.to || "?"}`),
      m.report ? el("a", { class: "ref-link", href: refHref(m.report) }, m.report) : null,
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
        } catch (err) { toastError(err, 5000); retract.disabled = false; }
      };
      head.append(el("span", { class: "msg-ops" }, retract));
    } else if (m.delivered) {
      head.append(el("a", { href: `#/run/${m.delivered.run_id}`,
        title: "the recipient's run that picked it up" }, "picked up ↗"));
      if (m.delivered.ts) head.append(when(m.delivered.ts));
    }
    card.append(head);
    if (m.title) card.append(el("div", { class: "msg-title" }, m.title));
    if (m.text) card.append(el("div", { class: "msg-detail" }, m.text));
    return card;
  }

  load();
  return { reload: load, destroy: () => revokeBlobs(blobs) };
}
