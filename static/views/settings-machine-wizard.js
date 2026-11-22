// Settings -> Machines setup WIZARD: a dialog that shows ONE short step at a time.
//   1 Machine  - host (and the name routines bind); Next scans and pins its host key
//   2 Account  - a new restricted user, or an existing account; Next saves the machine
//   3 Key      - the daemon generates the login key into Secrets; type a password once and it
//                installs the key, or paste one line on the box
//   4 Done     - the connection a run makes, tested
// Backend: web/settings/machine_access.py (key / install-command / install) beside the catalog
// routes in web/settings/machines.py (save / scan / test). A row's "set up access" opens it
// at step 3.

import { api } from "/static/api.js";
import { openModal } from "/static/components/dialog.js";
import { act, el, toast } from "/static/util.js";

const POLL_MS = 5000, POLL_FOR_MS = 180000;   // after a copy: re-test until the key lands
const TITLES = ["Machine", "Login account", "Install the key", "Done"];

const enc = encodeURIComponent;
const input = (ph, value = "", extra = {}) => el("input", { type: "text", placeholder: ph, value, style: "width:100%", ...extra });
const nameFrom = (host) => host.toLowerCase().split(".")[0].replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");

/** Open the wizard. `machine` set = an existing catalog row (start at the key step); `onDone`
 *  runs after the dialog closes on a finished setup. */
export function openMachineWizard({ machine = null, onDone }) {
  const st = { name: machine?.name || "", host: machine?.host || "", port: machine?.port || 22,
    user: machine?.user || "agent", mode: machine ? "account" : "agent", admin: "",
    keyNote: "", finished: false };
  let polling = false;                          // one re-test loop at a time (see check)
  const heading = el("strong", {});
  const progress = el("div", { class: "muted small" });
  const body = el("div", { class: "mt" });
  const fill = (...nodes) => body.replaceChildren(...nodes.filter(Boolean));   // skips absent lines
  const msg = el("div", { class: "small mt", style: "min-height:1.4em", "data-wiz-out": "" });
  const back = el("button", { class: "btn" }, "back");
  const next = el("button", { class: "btn primary", "data-wiz-next": "" }, "next");
  const xBtn = el("button", { class: "btn small", title: "close" }, "✕");
  const panel = el("div", { class: "panel", style: "width:520px", "data-mach-wizard": machine?.name || "" },
    el("div", { class: "row", style: "justify-content:space-between" }, heading, xBtn), progress,
    body, msg, el("div", { class: "row mt", style: "justify-content:space-between" }, back, next));
  const close = openModal(panel, { label: heading, onCancel: () => finish() });
  xBtn.onclick = () => finish();
  show(machine ? 2 : 0);

  function finish() { polling = false; close(); if (st.finished) onDone(); }
  function say(ok, text) {
    msg.style.color = ok == null ? "" : ok ? "var(--ok)" : "var(--err)";
    msg.textContent = text;
  }

  // One step on screen: its fields in `body`, its action on `next`.
  function show(i, note = "") {
    heading.textContent = machine ? `Set up access to ${machine.name}` : "Set up a machine";
    progress.textContent = `Step ${i + 1} of 4 · ${TITLES[i]}`;
    body.setAttribute("data-wiz-step", ["host", "account", "install", "done"][i]);
    say(note ? true : null, note);
    back.style.visibility = i === 1 || (i === 2 && !machine) ? "" : "hidden";
    back.onclick = () => { polling = false; show(i - 1); };
    next.style.display = "";
    [hostStep, accountStep, keyStep, doneStep][i]();
  }

  function hostStep() {
    const hostIn = input("host name or IP, e.g. 192.168.0.50", st.host);
    const nameIn = input("name", st.name);
    const portIn = el("input", { type: "number", value: String(st.port), style: "width:90px" });
    let named = !!st.name;                     // follow the host until the name is typed
    hostIn.oninput = () => { if (!named) nameIn.value = nameFrom(hostIn.value); };
    nameIn.oninput = () => { named = true; };
    fill(
      el("label", { class: "field" }, el("span", {}, "Which machine?"), hostIn),
      el("div", { class: "row", style: "gap:8px" },
        el("label", { class: "field", style: "flex:1" }, el("span", {}, "Name routines use"), nameIn),
        el("label", { class: "field" }, el("span", {}, "SSH port"), portIn)));
    hostIn.focus();
    next.textContent = "next";
    next.onclick = () => act(next, async () => {
      st.host = hostIn.value.trim(); st.name = nameIn.value.trim(); st.port = parseInt(portIn.value, 10) || 22;
      if (!st.host || !st.name) { say(false, "enter the machine and a name"); return; }
      say(null, `looking for ${st.host}…`);
      const r = await api("/api/settings/machines/scan", { method: "POST", body: { host: st.host, port: st.port } });
      if (!r.ok) { say(false, `✗ ${r.error}`); return; }
      st.hostKey = r.host_key;
      show(1, `✓ found ${st.host} — its identity is pinned`);
    });
  }

  function accountStep() {
    const userIn = input("account name", st.mode === "agent" ? st.user : "");
    const warn = el("div", { class: "small mt", style: "color:var(--warn)" },
      "Routines will act with this account's full rights. An admin account (sudo or docker) can ",
      "change anything on the box — including the scheduler, if it runs there.");
    const sync = () => { warn.style.display = st.mode === "account" ? "" : "none"; };
    const choice = (mode, title, detail) => {
      const rb = el("input", { type: "radio", name: "wiz-mode", "data-wiz-mode": mode, checked: st.mode === mode });
      rb.onchange = () => { st.mode = mode; userIn.value = mode === "agent" ? "agent" : ""; sync(); userIn.focus(); };
      return el("label", { class: "row mt", style: "gap:8px;align-items:flex-start" }, rb,
        el("span", {}, el("strong", {}, title), el("div", { class: "muted small" }, detail)));
    };
    fill(el("div", {}, "Which account should the scheduler log in as?"),
      choice("agent", "A new restricted user (recommended)", "No sudo, key-only, its own home. Created for you."),
      choice("account", "An account that already exists", "e.g. your own user."),
      el("label", { class: "field mt" }, el("span", {}, "Account name"), userIn), warn);
    sync();
    next.textContent = "next";
    next.onclick = () => act(next, async () => {
      st.user = userIn.value.trim();
      if (!st.user) { say(false, "enter the account name"); return; }
      const spec = { name: st.name, host: st.host, port: st.port, user: st.user,
        host_key: st.hostKey, workdir: `/home/${st.user}` };
      const r = await api(`/api/settings/machines/${enc(st.name)}`, { method: "PUT", body: spec });
      if (r.problems?.length) toast(r.problems.join("; "), 6000, { error: true });
      show(2);
    });
  }

  async function keyStep() {
    fill(el("div", { class: "muted small" }, "creating the login key…"));
    next.style.display = "none";
    let k;
    try { k = await api(`/api/settings/machines/${enc(st.name)}/key`, { method: "POST" }); }
    catch (err) { say(false, `✗ ${err.message}`); return; }
    st.keyNote = k.generated ? `A login key was created and saved as the secret ${k.key_var}.`
                             : `Using the login key saved as ${k.key_var}.`;
    passwordView();
  }

  // Default: type a password once, the daemon does the rest.
  function passwordView() {
    const agent = st.mode === "agent";
    const adminIn = input("admin account, e.g. mark", st.admin);
    const pwIn = el("input", { type: "password", placeholder: "password", autocomplete: "new-password", style: "width:100%" });
    const pasteLink = el("a", { href: "#", "data-wiz-paste": "" }, "I'd rather paste a command on the machine");
    pasteLink.onclick = (e) => { e.preventDefault(); say(null, ""); pasteView(); };
    fill(el("div", { class: "muted small" }, st.keyNote),
      el("div", { class: "mt" }, agent
        ? `To create ${st.user} on ${st.host}, log in once as an admin who can sudo:`
        : `Log in once as ${st.user} on ${st.host}:`),
      agent ? el("label", { class: "field mt" }, el("span", {}, "Admin account"), adminIn) : null,
      el("label", { class: agent ? "field" : "field mt" }, el("span", {}, "Password"), pwIn),
      el("div", { class: "muted small" }, "Used for this one login, then forgotten."),
      el("div", { class: "mt small" }, pasteLink),
      machine ? modeToggle(passwordView) : null);
    (agent ? adminIn : pwIn).focus();
    next.style.display = ""; next.textContent = "install key";
    next.onclick = () => act(next, async () => {
      st.admin = adminIn.value.trim();
      if (agent && !st.admin) { say(false, "enter the admin account"); return; }
      if (!pwIn.value) { say(false, "enter the password"); return; }
      say(null, "logging in and installing…");
      const r = await api(`/api/settings/machines/${enc(st.name)}/install`, { method: "POST",
        body: { mode: st.mode, password: pwIn.value, login_user: st.admin, coowner: agent ? st.admin : "" } });
      pwIn.value = "";
      if (!r.ok) { say(false, `✗ ${r.error}`); return; }
      say(null, "installed — testing the connection…");
      await check(false);
    });
  }

  // Fallback: one line to paste on the box, then the wizard re-tests until it lands.
  async function pasteView() {
    const cmdBox = el("textarea", { class: "code", rows: "3", readonly: true, style: "width:100%;min-height:0", "data-wiz-command": "" });
    const runAs = el("div", { class: "mt" });
    const pwLink = el("a", { href: "#" }, "use a password instead");
    pwLink.onclick = (e) => { e.preventDefault(); polling = false; say(null, ""); passwordView(); };
    fill(el("div", { class: "muted small" }, st.keyNote), runAs, cmdBox,
      el("div", { class: "muted small" }, "One long line — “copy command” takes all of it."),
      el("div", { class: "mt small" }, pwLink), machine ? modeToggle(pasteView) : null);
    next.style.display = ""; next.textContent = "copy command";
    next.onclick = async () => {
      try { await navigator.clipboard.writeText(cmdBox.value); } catch { cmdBox.select(); }
      say(null, "copied — paste it on the machine; this window notices when it is done…");
      check(true);
    };
    try {
      const c = await api(`/api/settings/machines/${enc(st.name)}/install-command?mode=${enc(st.mode)}`);
      cmdBox.value = c.command;
      runAs.textContent = `Open a terminal on ${c.host}, ${c.run_as}, and paste this:`;
    } catch (err) { say(false, `✗ ${err.message}`); }
  }

  // A catalog row may not know whether its user exists yet: one line to switch.
  function modeToggle(redraw) {
    const a = el("a", { href: "#", "data-wiz-mode-toggle": "" }, st.mode === "account"
      ? `${st.user} doesn't exist yet? create it as a restricted user`
      : `${st.user} already exists? add the key to it instead`);
    a.onclick = (e) => { e.preventDefault(); st.mode = st.mode === "account" ? "agent" : "account"; redraw(); };
    return el("div", { class: "small mt" }, a);
  }

  // The exact connection a run makes; `poll` keeps trying while the operator pastes.
  async function check(poll) {
    if (poll && polling) return;
    polling = poll;
    const until = Date.now() + POLL_FOR_MS;
    for (;;) {
      if (!panel.isConnected) return;          // the dialog was closed: nothing left to report
      const r = await api(`/api/settings/machines/${enc(st.name)}/test`, { method: "POST" })
        .catch((err) => ({ ok: false, error: err.message }));
      if (!panel.isConnected) return;
      if (r.ok) { polling = false; show(3); return; }
      if (!poll || !polling || Date.now() > until) {
        if (!poll || Date.now() > until) say(false, `✗ not reachable with the key yet: ${r.error}`);
        polling = false; return;
      }
      await new Promise((res) => setTimeout(res, POLL_MS));
    }
  }

  function doneStep() {
    st.finished = true;
    fill(
      el("div", { style: "color:var(--ok)" }, `✓ Connected as ${st.user}@${st.host}`),
      el("div", { class: "mt" }, `To let a routine use ${st.name}: open the routine → `,
        "Limits & reach → more → Machines, tick it and accept."));
    back.style.visibility = "hidden";
    next.textContent = "done";
    next.onclick = () => finish();
    next.focus();
  }
}
