// The agent desktops: every running desktop — one VM per routine (or conversation, or
// background task) — as a card with its INTERACTIVE screen. This is the take-over surface: the
// corner dock is the read-only glance, and here the keyboard and the mouse are the operator's,
// for the moment a routine is stuck on a dialog only a person can answer.
//
// Each screen loads through the console's own origin (`/desktop-view/…`, F527) with the
// desktop's own screen token — the one /api/desktops reports, which only the operator's token
// can read — and one pass for all of them (F530). See components/screen.js.
//
// The list is the console's ONE reader of /api/desktops (desktops-store.js), shared with the
// dock; this view subscribes while it is mounted and paints only while it is (CLAUDE.md: a view
// acts only while mounted). Cards are RECONCILED, never rebuilt: re-creating or even moving an
// <iframe> reloads it, which would drop the VNC connection under the operator's hands at every
// poll. A card's frame is replaced only when its desktop came back as a different machine (a
// new screen token).

import { api } from "/static/api.js";
import { act, chip, el, emptyState, fmtDur, skeleton, toast } from "/static/util.js";
import { confirmDialog } from "/static/components/dialog.js";
import { DESKTOP, grantPass, screenSrc } from "/static/components/screen.js";
import { labelOf, loadDesktops, subscribeDesktops } from "/static/desktops-store.js";

// The run chip's vocabulary: a desktop that is up is the machine working (signal), one that is
// booting is too, and one powering off is winding down to nothing.
function stateChip(d) {
  if (d.stopping) return chip("stopping", "idle");
  if (!d.ready) return chip("starting", "starting");
  return chip("running", "running");
}

function metaLine(d) {
  return `slot ${d.slot ?? "?"} · up ${fmtDur(d.up_s) || "0s"} · idle ${fmtDur(d.idle_s) || "0s"}`;
}

function foldersLine(d) {
  const folders = d.folders || [];
  if (!folders.length) return el("div", { class: "faint small" }, "no folders mounted");
  return el("div", { class: "desktop-folders small" },
    el("span", { class: "faint" }, "folders "),
    ...folders.map((f) => el("span", { class: "desktop-folder mono",
      title: `${f.path || ""} → ${f.guest_path || ""}` },
      `${f.name}${f.rw ? "" : " (read-only)"} → ${f.guest_path || "?"}`)));
}

// One card per desktop. `update` repaints the words and mounts the screen the first time the
// desktop is ready; the frame itself is never touched again.
function desktopCard(d) {
  const meta = el("span", { class: "mono small faint" });
  const chipBox = el("span", {});
  const folders = el("div", {});
  const screen = el("div", { class: "desktop-screen-wrap" });
  const stop = el("button", { class: "btn small danger", type: "button",
    title: "power this desktop off now — its next desktop command starts it again" }, "stop");
  // the confirm runs INSIDE act(), so the button stays disabled while its dialog is open and a
  // second press is one question, not two
  stop.onclick = () => act(stop, async () => {
    const ok = await confirmDialog(`Stop ${labelOf(d)}'s desktop? Whatever is open on it `
      + "closes now; its home folder is kept, and its next desktop command starts it again.",
      { confirmLabel: "stop" });
    if (!ok) return;
    const res = await api(`/api/desktops/${encodeURIComponent(d.name)}/stop`,
      { method: "POST", body: {} });
    toast(res.stopped ? "desktop stopped" : "that desktop was no longer running");
    loadDesktops();
  });
  const owner = d.owner?.href
    ? el("a", { href: d.owner.href, class: "desktop-owner" }, labelOf(d))
    : el("span", { class: "desktop-owner" }, labelOf(d));
  const card = el("section", { class: "panel desktop-card", "data-desktop": d.name },
    el("div", { class: "desktop-head" }, owner, chipBox, meta, stop),
    folders, screen);

  let framed = false;
  const update = (row) => {
    meta.textContent = metaLine(row);
    chipBox.replaceChildren(stateChip(row));
    folders.replaceChildren(foldersLine(row));
    if (framed) return;
    if (!row.vnc) {
      screen.replaceChildren(el("div", { class: "desktop-screen-note faint small" },
        "this desktop reported no usable screen token, so its screen cannot be opened"));
    } else if (!row.ready) {
      screen.replaceChildren(el("div", { class: "desktop-screen-note faint small" },
        "starting — the screen appears once the desktop is up"));
    } else {
      framed = true;
      // NOT sandboxed down to a picture: taking the keyboard is what this page is for
      screen.replaceChildren(el("iframe", { class: "desktop-screen",
        title: `${labelOf(row)} — desktop screen`, allow: "clipboard-read; clipboard-write",
        src: screenSrc(DESKTOP, { token: row.vnc }) }));
    }
  };
  update(d);
  return { card, vnc: d.vnc, update };
}

export async function render(view) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("h1", {}, "Desktops"),
      el("div", { class: "sub" },
        "each routine's own computer — watch it work, or take the keyboard"))));
  const box = el("div", { class: "mt" }, skeleton(["100%", "100%"]));
  view.append(box);

  let status;
  try { status = await api("/api/status"); }
  catch (err) {
    box.replaceChildren(emptyState("✕", "Couldn't reach the daemon", err.message));
    return undefined;
  }
  if (!status.desktops) {
    // Not configured is a legitimate state, not a failure: most instances run no desktops.
    box.replaceChildren(emptyState("◌", "No agent desktops are configured",
      "Routines get a desktop of their own from the desktop service (the `desktop` compose "
      + "profile). Point this console at it under Settings → Server → desktop broker URL and "
      + "desktop screen (noVNC) URL."),
      el("div", { class: "row mt" },
        el("a", { class: "btn small primary", href: "#/settings?section=server" }, "open Settings")));
    return undefined;
  }
  try { await grantPass(DESKTOP); }
  catch (err) {
    box.replaceChildren(emptyState("✕", "Couldn't get access to the screens",
      `${err.message} — the desktop screens need a pass from this console, and minting it failed.`));
    return undefined;
  }

  const summary = el("div", { class: "faint small" });
  const problem = el("div", {});
  const list = el("div", { class: "desktop-list" });
  box.replaceChildren(summary, problem, list);
  const cards = new Map();          // desktop name → { card, vnc, update }

  const paint = (fleet) => {
    if (!list.isConnected) return;  // navigated away: this view no longer owns a page
    problem.replaceChildren(fleet.error
      ? el("div", { class: "panel warn small" }, `couldn't read the desktops: ${fleet.error}`)
      : "");
    if (fleet.error) return;        // keep the screens: a failed poll is not a stopped desktop
    const rows = fleet.desktops || [];
    const limit = fleet.idle_limit_s ? ` · a desktop stops after ${fmtDur(fleet.idle_limit_s)} idle`
      : "";
    summary.textContent = `${rows.length} of ${fleet.slots ?? "?"} desktop slot`
      + `${fleet.slots === 1 ? "" : "s"} in use${limit}`;
    const names = new Set(rows.map((d) => d.name));
    for (const [name, c] of cards) {
      const row = rows.find((d) => d.name === name);
      if (!names.has(name) || row.vnc !== c.vnc) { c.card.remove(); cards.delete(name); }
    }
    if (!rows.length) {
      list.replaceChildren(emptyState("◌", "No desktop is running",
        "Desktops start on a routine's first desktop command and stop on their own once idle."));
      return;
    }
    list.querySelector(":scope > .empty")?.remove();
    for (const d of rows) {
      const known = cards.get(d.name);
      if (known) { known.update(d); continue; }
      const made = desktopCard(d);
      cards.set(d.name, made);
      list.append(made.card);       // appended, never re-ordered: moving a frame reloads it
    }
  };

  const unsubscribe = subscribeDesktops(paint);
  loadDesktops();
  return unsubscribe;
}
