// Settings -> remote machines (SSH catalog) - split from settings.js (one section per module; settings.js keeps
// the section order, nav, and deep-link jump). Appends its own panel and returns the fill
// promise so settings.js can await all sections before the anchor jump.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, skeleton, toast } from "/static/util.js";

export function renderMachines(view) {
  // -- remote machines (SSH catalog) ----------------------------------------------
  const machBox = el("div", { class: "panel" });
  machBox.append(skeleton(["60%", "85%"]));
  view.append(machBox);
  async function fill() {
    let d;
    try { d = await api("/api/settings/machines"); }
    catch (err) { machBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    machBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
      "SSH hosts a routine can act on (a GPU box, a build server). Add one here, set its private ",
      "key as the ", el("code", {}, "key_var"), " secret in ",
      el("a", { href: "#/settings?section=secrets" }, "Secrets"),
      ", scan its host key, then bind it on a routine's page. The host key is PINNED — a run ",
      "refuses to connect on a mismatch."));

    // Existing machines — status + test/edit/delete.
    if (!d.machines.length)
      machBox.append(el("div", { class: "muted small", "data-mach-empty": "" }, "no machines yet"));
    else machBox.append(el("div", { class: "tablewrap" }, el("table", { class: "list" }, el("tbody", {},
      d.machines.map((m) => {
        const testOut = el("span", { class: "small mono" });
        const testBtn = el("button", { class: "btn small" }, "test");
        testBtn.onclick = async () => {
          testBtn.disabled = true; testOut.style.color = ""; testOut.textContent = "testing…";
          try {
            const r = await api(`/api/settings/machines/${m.name}/test`, { method: "POST" });
            testOut.style.color = r.ok ? "var(--ok)" : "var(--err)";
            testOut.textContent = r.ok ? "✓ reachable" : `✗ ${r.error}`;
            testOut.title = (r.warnings || []).join("; ");
          } catch (err) { testOut.style.color = "var(--err)"; testOut.textContent = `✗ ${err.message}`; }
          testBtn.disabled = false;
        };
        const editBtn = el("button", { class: "btn small" }, "edit");
        editBtn.onclick = () => fillForm(m);
        const delBtn = el("button", { class: "btn small danger" }, "delete");
        delBtn.onclick = async () => {
          if (!(await confirmDialog(`Delete machine ${m.name}?`, { confirmLabel: "delete" }))) return;
          try { await api(`/api/settings/machines/${m.name}`, { method: "DELETE" }); fill(); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        };
        const flags = [
          m.has_host_key ? null : el("span", { class: "small", style: "color:var(--warn)" }, "no host key"),
          m.has_key ? null : el("span", { class: "small", style: "color:var(--warn)" },
            m.key_var ? `key ${m.key_var} unset` : "no key_var")].filter(Boolean);
        return el("tr", { "data-mach": m.name },
          el("td", {}, el("strong", {}, m.name),
            m.description ? el("div", { class: "muted small" }, m.description) : null,
            m.share ? el("div", { class: "muted small mono" }, `mnt/${m.name}/ ← ${m.share}`) : null),
          el("td", { class: "muted small mono" }, `${m.user}@${m.host}:${m.port}`),
          el("td", { class: "small" }, flags.length ? flags : el("span", { style: "color:var(--ok)" }, "✓ ready")),
          el("td", {}, el("div", { class: "row" }, testBtn, editBtn, delBtn), testOut));
      })))));

    // Add / edit form. PUT upserts by name; editing a row prefills it.
    const inp = (ph, w) => el("input", { type: "text", placeholder: ph, style: `width:${w}` });
    const nameIn = inp("name (gpu-box)", "130px"), hostIn = inp("host / IP", "150px");
    const userIn = inp("ssh user", "110px"), portIn = el("input", { type: "number", value: "22", style: "width:70px" });
    const keyVarIn = inp("KEY_VAR (Secrets)", "170px"), wdIn = inp("workdir (optional)", "170px");
    const shareIn = inp("share to mount, e.g. /srv/shared (optional)", "260px");
    const descIn = inp("one-line description", "280px"), tagsIn = inp("tags, comma-separated", "200px");
    const hkIn = el("textarea", { class: "code", rows: "2", style: "width:100%",
      placeholder: "ssh-ed25519 AAAA…  (click scan, or paste ssh-keyscan output)" });
    const scanOut = el("span", { class: "small mono" });
    const scanBtn = el("button", { class: "btn small" }, "scan host key");
    scanBtn.onclick = async () => {
      if (!hostIn.value.trim()) { toast("enter the host first"); return; }
      scanBtn.disabled = true; scanOut.style.color = ""; scanOut.textContent = "scanning…";
      try {
        const r = await api("/api/settings/machines/scan", { method: "POST",
          body: { host: hostIn.value.trim(), port: parseInt(portIn.value, 10) || 22 } });
        if (r.ok) { hkIn.value = r.host_key; scanOut.style.color = "var(--ok)"; scanOut.textContent = "✓ scanned — review & save"; }
        else { scanOut.style.color = "var(--err)"; scanOut.textContent = `✗ ${r.error}`; }
      } catch (err) { scanOut.style.color = "var(--err)"; scanOut.textContent = `✗ ${err.message}`; }
      scanBtn.disabled = false;
    };
    function fillForm(m) {
      nameIn.value = m.name; hostIn.value = m.host; userIn.value = m.user; portIn.value = m.port;
      keyVarIn.value = m.key_var || ""; wdIn.value = m.workdir || ""; descIn.value = m.description || "";
      shareIn.value = m.share || ""; tagsIn.value = (m.tags || []).join(", "); hkIn.value = m.host_key || "";
      machBox.scrollIntoView({ behavior: "smooth", block: "end" });
    }
    const saveBtn = el("button", { class: "btn primary" }, "save machine");
    saveBtn.onclick = async () => {
      const name = nameIn.value.trim();
      if (!name || !hostIn.value.trim() || !userIn.value.trim()) { toast("name, host and user are required"); return; }
      const body = { name, host: hostIn.value.trim(), user: userIn.value.trim(),
        port: parseInt(portIn.value, 10) || 22, key_var: keyVarIn.value.trim(),
        host_key: hkIn.value.trim(), share: shareIn.value.trim(), workdir: wdIn.value.trim(),
        description: descIn.value.trim(),
        tags: tagsIn.value.split(",").map((t) => t.trim()).filter(Boolean) };
      try {
        const r = await api(`/api/settings/machines/${name}`, { method: "PUT", body });
        (r.problems || []).forEach((p) => toast(p, 5000, { error: true }));
        toast(`machine ${name} saved`);
        [nameIn, hostIn, userIn, keyVarIn, wdIn, shareIn, descIn, tagsIn, hkIn].forEach((i) => (i.value = ""));
        portIn.value = "22"; fill();
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    machBox.append(
      el("div", { class: "mt small", style: "font-weight:600" }, "Add / edit a machine"),
      el("div", { class: "row mt", style: "flex-wrap:wrap;gap:6px" }, nameIn, hostIn, userIn, portIn),
      el("div", { class: "row mt", style: "flex-wrap:wrap;gap:6px" }, keyVarIn, wdIn, tagsIn),
      el("div", { class: "row mt", style: "flex-wrap:wrap;gap:6px" }, shareIn,
        el("span", { class: "muted small", style: "align-self:center" },
          "a share mounts at mnt/<name>/ for bound routines")),
      el("div", { class: "row mt" }, descIn),
      el("div", { class: "field mt" }, el("span", {}, "host key (pinned)"), hkIn),
      el("div", { class: "row mt" }, scanBtn, scanOut),
      el("div", { class: "row mt" }, saveBtn));
  }
  return fill();
}
