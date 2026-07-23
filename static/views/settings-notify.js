// Settings -> notifications: tier 1 (OS notifications while a tab is open) + tier 2
// (Web Push through the service worker) - split from settings.js. Returns the panel
// node synchronously (no data fetch gates first paint).

import { api } from "/static/api.js";
import * as notify from "/static/notify.js";
import { el, toast } from "/static/util.js";

// ---- notifications: tier 1 (tab open) + tier 2 (Web Push, tab closed) -------------------------
export function renderNotifications() {
  const panel = el("div", { class: "panel" });
  panel.append(el("div", { class: "muted small", style: "margin-bottom:8px" },
    "Both are OPT-IN and per browser. Tab-open notifications fire from this console while any ",
    "tab is open; Web Push reaches this browser even with the console closed — like the Discord ",
    "mirror, nothing is sent until you enable it here."));

  // tier 1 — the Notification API on the live event stream
  const t1box = el("input", { type: "checkbox", checked: notify.enabled() ? "" : null,
    disabled: notify.supported() ? null : "" });
  t1box.onchange = async () => {
    const on = await notify.setEnabled(t1box.checked);
    t1box.checked = on;
    toast(on ? "tab-open notifications enabled" :
      t1box.checked === false && Notification.permission === "denied"
        ? "the browser blocked notifications — allow them in the site settings"
        : "tab-open notifications off");
  };
  panel.append(el("label", { class: "row", style: "gap:8px" }, t1box,
    el("div", {},
      el("div", { class: "t-title" }, "OS notifications while a console tab is open"),
      el("div", { class: "muted small" }, notify.supported()
        ? "new decisions pop up in the system tray; clicking one opens the Decisions page"
        : "this browser has no Notification API"))));

  // tier 2 — Web Push through the service worker
  const pushRow = el("div", { class: "mt" }, el("span", { class: "muted small" }, "Web Push — checking…"));
  panel.append(pushRow);
  const renderPush = async () => {
    pushRow.replaceChildren();
    if (!window.isSecureContext) {
      pushRow.append(el("div", { class: "muted small" },
        "Web Push needs a secure context — serve the console over HTTPS (or open it via localhost, ",
        "e.g. an SSH tunnel) to enable push with the browser closed."));
      return;
    }
    const st = await notify.pushStatus();
    if (!st.supported) {
      pushRow.append(el("div", { class: "muted small" }, "this browser does not support Web Push"));
      return;
    }
    const info = await api("/api/push").catch(() => null);
    const head = el("div", { class: "t-title" },
      `Web Push (works with the browser closed) — this browser: ${st.subscribed ? "subscribed" : "not subscribed"}`
      + (info ? ` · ${info.subscriptions} browser(s) total` : ""));
    const sub = el("button", { class: "btn small primary", hidden: st.subscribed || null }, "enable on this browser");
    const unsub = el("button", { class: "btn small danger", hidden: st.subscribed ? null : "" }, "disable on this browser");
    const test = el("button", { class: "btn small", hidden: st.subscribed ? null : "" }, "send test");
    sub.onclick = async () => {
      sub.disabled = true;
      try { await notify.pushSubscribe(); toast("subscribed — decisions push to this browser now"); renderPush(); }
      catch (err) { toast(err.message, 5000, { error: true }); sub.disabled = false; }
    };
    unsub.onclick = async () => {
      unsub.disabled = true;
      try { await notify.pushUnsubscribe(); toast("push disabled on this browser"); renderPush(); }
      catch (err) { toast(err.message, 5000, { error: true }); unsub.disabled = false; }
    };
    test.onclick = async () => {
      try { const r = await api("/api/push/test", { method: "POST" });
        toast(`test sent to ${r.sent} browser(s)`); }
      catch (err) { toast(err.message, 5000, { error: true }); }
    };
    pushRow.append(head,
      el("div", { class: "muted small" },
        "one notification per new decision, sent by the server — subscribe each browser/device you want reached"),
      el("div", { class: "row mt" }, sub, unsub, test));
  };
  renderPush();
  return panel;
}
