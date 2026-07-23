// Settings -> the library repository + its scheduled sync - split from settings.js (one section per module; settings.js keeps
// the section order, nav, and deep-link jump). Appends its own panel and returns the fill
// promise so settings.js can await all sections before the anchor jump.

import { api } from "/static/api.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { remount } from "/static/router.js";
import { el, skeleton, toast } from "/static/util.js";
import { remoteTester } from "/static/views/settings-common.js";

export function renderLibraries(view) {
  const libBox = el("div", { class: "panel" });
  libBox.append(skeleton(["60%", "90%"]));
  view.append(libBox);
  async function fill() {
    try {
      const { libraries } = await api("/api/settings/libraries");
      libBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
        "One git repo holds everything the instance acquires: workflows/, traits/, permissions/, utils/ ",
        "(with the gu dispatcher) — plus routines/ and sanitized config, exported by the scheduled ",
        "Library sync below. ",
        "Clone your existing repo, or create a new private one seeded with the built-in defaults. ",
        "(Connect GitHub above first.)"));
      for (const lib of libraries) {
        if (!lib.provisioned) {
          const repoIn = el("input", { type: "text", placeholder: "owner/name or full URL", style: "flex:1" });
          const cloneB = el("button", { class: "btn small" }, "clone existing");
          const createB = el("button", { class: "btn small primary" }, "create + seed");
          const doProv = async (mode) => {
            const repo = repoIn.value.trim();
            if (!repo) { toast("enter a repo (owner/name or URL)"); return; }
            cloneB.disabled = createB.disabled = true;
            try {
              await api(`/api/settings/libraries/${lib.name}/provision`, { method: "POST", body: { repo, mode } });
              toast(`${lib.name}: ${mode === "clone" ? "cloned" : "created + seeded"}`); remount();
            } catch (err) { toast(err.message, 7000, { error: true }); cloneB.disabled = createB.disabled = false; }
          };
          cloneB.onclick = () => doProv("clone");
          createB.onclick = () => doProv("create");
          libBox.append(el("div", { class: "row", style: "margin:9px 0" },
            el("span", { class: "ref-tag", style: "min-width:90px;text-align:center" }, lib.name),
            repoIn, cloneB, createB));
          libBox.append(el("div", { class: "faint small", style: "margin:-4px 0 8px 98px" },
            "not set up yet"));
          continue;
        }
        const input = el("input", { type: "text", value: lib.remote || "",
          placeholder: "https://github.com/<you>/<repo>.git — empty = local only" });
        const save = el("button", { class: "btn small primary" }, "save + push");
        save.onclick = async () => {
          try { const r = await api(`/api/settings/libraries/${lib.name}`, { method: "PUT", body: { remote: input.value.trim() } });
            toast(r.pushed ? `${lib.name}: saved + pushed` : r.push_error ? `${lib.name}: saved (push failed: ${r.push_error})` : `${lib.name}: saved`); }
          catch (err) { toast(err.message, 5000, { error: true }); }
        };
        const t = remoteTester(input);
        libBox.append(el("div", { class: "row", style: "margin:9px 0" },
          el("span", { class: "ref-tag", style: "min-width:90px;text-align:center" }, lib.name),
          input, t.btn, save));
        libBox.append(el("div", { style: "margin:-4px 0 8px 98px" }, t.result));
      }
    } catch (err) { libBox.replaceChildren(el("div", { class: "muted" }, err.message)); }
  }
  return fill();
}

export function renderLibrarySync(view, serverTz) {
  // -- scheduled library sync (a plain daemon job — the same commands every time) ---
  const lsBox = el("div", { class: "panel" });
  lsBox.append(skeleton(["50%", "80%"]));
  view.append(lsBox);
  async function fill() {
    let ls;
    try { ls = await api("/api/settings/library-sync"); }
    catch (err) { lsBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    const enabled = el("input", { type: "checkbox", ...(ls.enabled ? { checked: true } : {}) });
    const known = ["manual", "hourly", "daily", "weekly", "monthly"];
    const friendly = known.includes(ls.schedule_friendly?.frequency)
      ? ls.schedule_friendly : { frequency: "daily", time: "06:00" };
    const sched = scheduleEditor(friendly, serverTz);
    const save = el("button", { class: "btn small primary" }, "save");
    save.onclick = async () => {
      save.disabled = true;
      try {
        await api("/api/settings/library-sync", { method: "PUT",
          body: { enabled: enabled.checked, schedule: { friendly: sched.value() } } });
        toast("library sync schedule saved");
        fill();
      } catch (err) { toast(err.message, 5000, { error: true }); save.disabled = false; }
    };
    const runNow = el("button", { class: "btn small" }, "sync now");
    runNow.onclick = async () => {
      runNow.disabled = true; runNow.textContent = "syncing…";
      try {
        const r = await api("/api/settings/library-sync/run", { method: "POST" });
        toast(`library sync: ${r.status}${r.error ? ` — ${r.error}` : ""}`, r.status === "ok" ? 4000 : 8000,
              { error: r.status === "error" });
        fill();
      } catch (err) { toast(err.message, 7000, { error: true }); runNow.disabled = false; runNow.textContent = "sync now"; }
    };
    const lastLine = !ls.last
      ? "no sync has run yet"
      : `last sync ${ls.last.ts} — ${ls.last.status}`
        + (ls.last.error ? ` (${ls.last.error})`
           : ls.last.sync?.pull_error ? ` (pull conflict: ${ls.last.sync.pull_error})`
           : ls.last.sync ? ` (${ls.last.sync.pushed ? "pushed" : ls.last.sync.has_remote ? "push failed" : "no remote — committed locally"})`
           : "");
    lsBox.replaceChildren(
      el("div", { class: "muted small", style: "margin-bottom:6px" },
        "Mirrors the instance (routines + sanitized config) into the library repo and commits, ",
        "pulls and pushes it — the exact same commands every time, so it runs as a plain ",
        "scheduled job, not a routine."),
      el("label", { class: "row", style: "gap:8px;margin:9px 0" }, enabled,
        el("span", {}, "sync on a schedule")),
      sched.node,
      el("div", { class: "row", style: "gap:8px;margin-top:9px" }, save, runNow),
      el("div", { class: "faint small", style: "margin-top:8px" }, lastLine),
      el("div", { class: "faint small" },
        ls.enabled && ls.next_fire ? `next: ${ls.next_fire}` : "not scheduled"));
  }
  return fill();
}
