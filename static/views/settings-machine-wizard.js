// Settings -> Machines setup WIZARD: host -> login account -> key on the box -> test.
// The daemon does every step it can (scans the host key, generates the login key and stores it
// in Secrets, installs it over a one-time password login, tests); the one thing it cannot do
// without a password - reach the box - is offered as a single copyable line instead.
// Backend: web/settings/machine_access.py (key / install-command / install) beside the catalog
// routes in web/settings/machines.py (save / scan / test). Opened for a NEW machine (all four
// steps) or for an existing row's "set up access" (the last two).

import { api } from "/static/api.js";
import { act, el, toast } from "/static/util.js";

const POLL_MS = 5000, POLL_FOR_MS = 180000;   // after a copy: re-test until the key lands

const enc = encodeURIComponent;
const inp = (ph, w, extra = {}) => el("input", { type: "text", placeholder: ph, style: `width:${w}`, ...extra });
const say = (node, ok, text) => { node.style.color = ok == null ? "" : ok ? "var(--ok)" : "var(--err)"; node.textContent = text; };
const step = (key, title, ...body) => el("div", { class: "field mt", "data-wiz-step": key },
  el("strong", {}, title), ...body);

/** The wizard panel. `machine` set = an existing catalog row (skip to the key); `onDone`
 *  re-reads the section; `onCancel` closes the panel. */
export function machineWizard({ machine = null, onDone, onCancel }) {
  const box = el("div", { class: "panel mt", "data-mach-wizard": machine ? machine.name : "" });
  const st = { name: machine?.name || "", host: machine?.host || "", port: machine?.port || 22,
    user: machine?.user || "agent", hostKey: machine?.host_key || "", coowner: "",
    mode: machine ? "account" : "agent" };   // a catalog row's user most likely exists already
  let polling = false;                        // one re-test loop at a time (see check)
  const cancel = el("button", { class: "btn small" }, "close");
  cancel.onclick = () => onCancel();
  box.append(el("div", { class: "row" }, el("strong", {},
    machine ? `Set up access to ${machine.name}` : "Set up a machine"), cancel));
  if (!machine) hostStep();
  else keyStep().catch((err) => box.append(el("div", { class: "small", style: "color:var(--err)" }, `✗ ${err.message}`)));
  return box;

  // 1 - where is it: name, host, port -> the box's pinned identity
  function hostStep() {
    const nameIn = inp("name (gpu-box)", "140px"), hostIn = inp("host / IP", "160px");
    const portIn = el("input", { type: "number", value: "22", style: "width:70px" });
    const out = el("div", { class: "small mono" });
    const btn = el("button", { class: "btn primary" }, "check host");
    btn.onclick = () => act(btn, async () => {
      st.name = nameIn.value.trim(); st.host = hostIn.value.trim(); st.port = parseInt(portIn.value, 10) || 22;
      if (!st.name || !st.host) { toast("name and host are required"); return; }
      say(out, null, "reading the host key…");
      const r = await api("/api/settings/machines/scan", { method: "POST", body: { host: st.host, port: st.port } });
      if (!r.ok) { say(out, false, `✗ ${r.error}`); return; }
      st.hostKey = r.host_key;
      say(out, true, `✓ ${r.host_key.split(/\s+/).slice(0, 2).join(" ").slice(0, 60)}…`);
      btn.remove(); [nameIn, hostIn, portIn].forEach((i) => (i.disabled = true));
      accountStep();
    });
    box.append(step("host", "1 · Where is the machine?",
      el("div", { class: "row mt", style: "flex-wrap:wrap;gap:6px" }, nameIn, hostIn, portIn, btn),
      el("div", { class: "muted small" }, "The name is what routines bind. Checking reads the box's ",
        "identity key and pins it: a run refuses to connect if it ever changes."), out));
  }

  // 2 - which account the scheduler logs in as; saving creates the login key
  function accountStep() {
    const agentRb = el("input", { type: "radio", name: "wiz-mode", checked: true, "data-wiz-mode": "agent" });
    const acctRb = el("input", { type: "radio", name: "wiz-mode", "data-wiz-mode": "account" });
    const agentUser = inp("agent", "110px", { value: "agent" });
    const coIn = inp("your admin account (optional)", "220px");
    const acctUser = inp("existing account", "140px");
    const warn = el("div", { class: "small", style: "color:var(--warn);display:none" },
      "Runs act with this account's FULL rights on the box. An admin account (sudo, or the ",
      "docker group) can change anything there — including the scheduler itself if it runs on ",
      "this box. Prefer a restricted user unless a routine truly needs this one.");
    acctRb.onchange = agentRb.onchange = () => { warn.style.display = acctRb.checked ? "" : "none"; };
    const descIn = inp("one-line description, shown to the model", "320px");
    const wdIn = inp("workdir (default: the user's home)", "240px");
    const shareIn = inp("share to mount, e.g. /srv/shared", "240px");
    const exclIn = el("input", { type: "checkbox" });
    const out = el("div", { class: "small mono" });
    const btn = el("button", { class: "btn primary" }, "save & create login key");
    btn.onclick = () => act(btn, async () => {
      st.mode = acctRb.checked ? "account" : "agent";
      st.user = (st.mode === "agent" ? agentUser.value : acctUser.value).trim();
      st.coowner = st.mode === "agent" ? coIn.value.trim() : "";
      if (!st.user) { toast("enter the account name"); return; }
      const home = `/home/${st.user}`;
      const body = { name: st.name, host: st.host, port: st.port, user: st.user, host_key: st.hostKey,
        description: descIn.value.trim(), workdir: wdIn.value.trim() || home, share: shareIn.value.trim(),
        exclusive: exclIn.checked, tags: [] };
      const r = await api(`/api/settings/machines/${enc(st.name)}`, { method: "PUT", body });
      if (r.problems?.length) toast(r.problems.join("; "), 6000, { error: true });
      btn.remove(); box.querySelectorAll('[data-wiz-step="account"] input').forEach((i) => (i.disabled = true));
      await keyStep();
    });
    box.append(step("account", "2 · Which account does the scheduler log in as?",
      el("label", { class: "row mt", style: "gap:8px" }, agentRb, el("span", {}, "a new ",
        el("strong", {}, "restricted"), " user (recommended) — no sudo, key-only, its own home")),
      el("div", { class: "row", style: "gap:6px;margin-left:24px" }, agentUser, coIn),
      el("div", { class: "muted small", style: "margin-left:24px" },
        "The co-owner joins the user's group and keeps full access to everything it makes."),
      el("label", { class: "row mt", style: "gap:8px" }, acctRb, el("span", {}, "an existing account"), acctUser),
      warn,
      el("details", { class: "mt" }, el("summary", { class: "small" }, "more options"),
        el("div", { class: "row mt", style: "flex-wrap:wrap;gap:6px" }, descIn, wdIn, shareIn),
        el("label", { class: "row mt", style: "gap:8px" }, exclIn, el("span", { class: "small" },
          "exclusive — submitted jobs queue and run one at a time (a GPU box)"))),
      el("div", { class: "row mt" }, btn), out));
  }

  // 3 - put the public key on the box: by password, or one pasted line
  async function keyStep() {
    const k = await api(`/api/settings/machines/${enc(st.name)}/key`, { method: "POST" });
    const head = el("div", { class: "muted small" }, k.generated
      ? `A login key was created and stored as the secret ${k.key_var} — you never handle it. `
      : `Using the login key already stored as ${k.key_var}. `,
      el("span", { class: "mono" }, k.fingerprint));
    const modeSel = el("select", { "data-wiz-install-mode": "" },
      el("option", { value: "agent" }, `create ${st.user} as a restricted user (needs an admin's sudo)`),
      el("option", { value: "account" }, `${st.user} already exists — add the key to it`));
    modeSel.value = st.mode;
    const coIn = inp("co-owner admin (optional)", "200px", { value: st.coowner });
    const loginIn = inp("admin account", "140px");
    const pwIn = el("input", { type: "password", placeholder: "password", autocomplete: "new-password", style: "width:180px" });
    const pwLabel = el("span", { class: "small" });
    const out = el("div", { class: "small mono", "data-wiz-out": "" });
    const cmdBox = el("textarea", { class: "code", rows: "4", readonly: true, style: "width:100%;min-height:0", "data-wiz-command": "" });
    const runAs = el("span", { class: "small" });
    const sync = async () => {
      st.mode = modeSel.value; st.coowner = coIn.value.trim();
      const agent = st.mode === "agent";
      loginIn.style.display = coIn.style.display = agent ? "" : "none";
      pwLabel.textContent = agent ? `an admin account on ${st.host} that can sudo, and its password:`
                                  : `${st.user}'s password on ${st.host}:`;
      const q = `mode=${enc(st.mode)}&coowner=${enc(st.coowner)}`;
      try {
        const c = await api(`/api/settings/machines/${enc(st.name)}/install-command?${q}`);
        cmdBox.value = c.command; runAs.textContent = `Run this on ${c.host}, ${c.run_as}:`;
      } catch (err) { cmdBox.value = ""; runAs.textContent = `✗ ${err.message}`; }
    };
    modeSel.onchange = sync; coIn.onchange = sync;

    const installBtn = el("button", { class: "btn primary" }, "install key");
    installBtn.onclick = () => act(installBtn, async () => {
      if (!pwIn.value) { toast("enter the password"); return; }
      say(out, null, "logging in and installing…");
      const r = await api(`/api/settings/machines/${enc(st.name)}/install`, { method: "POST",
        body: { mode: st.mode, password: pwIn.value, login_user: loginIn.value.trim(), coowner: st.coowner } });
      pwIn.value = "";
      if (!r.ok) { say(out, false, `✗ ${r.error}`); return; }
      say(out, true, "✓ installed — testing the new key…");
      await check(out, false);
    });
    const copyBtn = el("button", { class: "btn small" }, "copy");
    copyBtn.onclick = async () => {
      try { await navigator.clipboard.writeText(cmdBox.value); toast("copied — paste it on the box"); }
      catch { cmdBox.select(); toast("clipboard blocked — command selected, press Ctrl-C"); }
      check(out, true);
    };
    const checkBtn = el("button", { class: "btn small" }, "check now");
    checkBtn.onclick = () => act(checkBtn, () => check(out, false));

    box.append(step("install", `3 · Put the key on ${st.host}`, head,
      el("div", { class: "row mt", style: "flex-wrap:wrap;gap:6px" }, modeSel, coIn),
      el("div", { class: "mt" }, el("strong", { class: "small" }, "Do it for me"), " ", pwLabel),
      el("div", { class: "row", style: "flex-wrap:wrap;gap:6px" }, loginIn, pwIn, installBtn),
      el("div", { class: "muted small" }, "Used for this one login and kept nowhere. A box that ",
        "allows key login only refuses it — paste the line below instead."),
      el("div", { class: "mt" }, el("strong", { class: "small" }, "Or paste one line"), " ", runAs),
      cmdBox, el("div", { class: "row" }, copyBtn, checkBtn,
        el("span", { class: "muted small", style: "align-self:center" },
          "after a copy, the connection is re-checked every few seconds")),
      out));
    await sync();
  }

  // 4 - the exact connection a run makes; `poll` keeps trying while the operator pastes
  async function check(out, poll) {
    if (poll && polling) return;
    polling = poll;
    const until = Date.now() + POLL_FOR_MS;
    for (;;) {
      if (!box.isConnected) return;            // the wizard was closed: nothing left to report
      const r = await api(`/api/settings/machines/${enc(st.name)}/test`, { method: "POST" })
        .catch((err) => ({ ok: false, error: err.message }));
      if (r.ok) { polling = false; say(out, true, "✓ connected with the new key"); done(); return; }
      if (!poll || Date.now() > until) {
        polling = false; say(out, false, `✗ not reachable with the key yet: ${r.error}`); return;
      }
      say(out, null, "waiting for the key to land on the box…");
      await new Promise((res) => setTimeout(res, POLL_MS));
    }
  }

  function done() {
    if (box.querySelector('[data-wiz-step="done"]')) return;
    const fin = el("button", { class: "btn primary" }, "done");
    fin.onclick = () => onDone();
    box.append(step("done", `✓ ${st.user}@${st.host} answers with the new key`,
      el("div", { class: "muted small" }, "Last step: on a routine's page, Limits & reach → more → ",
        `Machines, tick ${st.name} and accept. Only bound routines can reach it.`),
      el("div", { class: "row mt" }, fin)));
  }
}
