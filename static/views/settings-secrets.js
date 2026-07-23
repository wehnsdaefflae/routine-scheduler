// Settings -> central secrets store - split from settings.js (one section per module; settings.js keeps
// the section order, nav, and deep-link jump). Appends its own panel and returns the fill
// promise so settings.js can await all sections before the anchor jump.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, skeleton, toast } from "/static/util.js";

export function renderSecrets(view) {
  // -- central secrets store ------------------------------------------------------
  const secBox = el("div", { class: "panel" });
  secBox.append(skeleton(["60%", "90%"]));
  view.append(secBox);
  async function fill() {
    let s;
    try { s = await api("/api/settings/secrets"); }
    catch (err) { secBox.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    secBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:6px" },
      "One store for every credential — injected into all utils, LLM endpoints, and the Claude ",
      "subscription (as CLAUDE_CODE_OAUTH_TOKEN) at run time. Values are write-only — never shown back."));

    const keyIn = el("input", { type: "text", placeholder: "KEY (e.g. CLAUDE_CODE_OAUTH_TOKEN)", style: "flex:1" });
    const valIn = el("input", { type: "password", placeholder: "value", style: "flex:1" });
    const delBtn = (k) => {
      const b = el("button", { class: "btn small danger" }, "delete");
      b.onclick = async () => {
        if (!(await confirmDialog(`Delete secret ${k}?`, { confirmLabel: "delete" }))) return;
        try { await api(`/api/settings/secrets/${encodeURIComponent(k)}`, { method: "DELETE" }); fill(); }
        catch (err) { toast(err.message, 4000, { error: true }); }
      };
      return b;
    };

    // What the installed utils DECLARE they need — so you know exactly what to add (unset flagged).
    if (s.needed?.length) {
      secBox.append(el("div", { class: "mt small", style: "font-weight:600" }, "Needed by installed utils"));
      secBox.append(el("div", { class: "tablewrap" },
        el("table", { class: "list" }, el("tbody", {}, s.needed.map((n) => {
          const setBtn = el("button", { class: "btn small" }, n.set ? "replace" : "set");
          setBtn.onclick = () => { keyIn.value = n.key; valIn.value = ""; valIn.focus(); };
          // The declaring util's usage + docstring — shows the expected FORMAT of a structured
          // secret (e.g. FTP_SOURCES's JSON shape) right where you set it.
          const fmt = (n.doc || n.usage)
            ? el("details", { class: "small", "data-secret-fmt": n.key },
                el("summary", { class: "muted", style: "cursor:pointer" }, "format / help"),
                el("pre", { style: "white-space:pre-wrap;font-size:11px;margin:4px 0;padding:6px 8px;background:var(--ink);border:1px solid var(--line);border-radius:6px" },
                  [n.usage, n.doc].filter(Boolean).join("\n\n")))
            : null;
          return el("tr", {},
            el("td", {}, el("div", { class: "mono" }, n.key), fmt),
            el("td", { class: "small", style: `color:${n.set ? "var(--ok)" : "var(--warn)"}` }, n.set ? "✓ set" : "unset"),
            el("td", { class: "muted small" }, n.utils.join(", ")),
            el("td", {}, n.set ? delBtn(n.key) : setBtn));
        })))));
    }

    // Anything you've set that no util declares (e.g. an endpoint's key_var, or a manual add).
    const declared = new Set((s.needed || []).map((n) => n.key));
    const extra = s.keys.filter((k) => !declared.has(k));
    if (extra.length) {
      secBox.append(el("div", { class: "mt small", style: "font-weight:600" }, "Other secrets set"));
      secBox.append(el("div", { class: "tablewrap" },
        el("table", { class: "list" }, el("tbody", {}, extra.map((k) =>
          el("tr", {}, el("td", {}, k), el("td", { class: "muted" }, "••••••••"), el("td", {}, delBtn(k))))))));
    }
    if (!s.needed?.length && !extra.length)
      secBox.append(el("div", { class: "muted mt small" }, "no secrets set yet"));

    const save = el("button", { class: "btn small primary" }, "set");
    save.onclick = async () => {
      const key = keyIn.value.trim();
      if (!key || !valIn.value) { toast("enter a KEY and a value"); return; }
      try {
        await api("/api/settings/secrets", { method: "PUT", body: { key, value: valIn.value } });
        toast(`${key} saved`); keyIn.value = ""; valIn.value = ""; fill();
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    // show/hide the value while typing — a JSON map is unreadable when masked
    const showVal = el("button", { class: "btn small", type: "button" }, "show");
    showVal.onclick = () => {
      const masked = valIn.type === "password";
      valIn.type = masked ? "text" : "password";
      showVal.textContent = masked ? "hide" : "show";
    };
    secBox.append(el("div", { class: "row mt" }, keyIn, valIn, showVal, save));

    // -- multi-entry (JSON-map) secrets: manage one entry at a time -----------------------
    // The store never returns values, so you extend a map secret (e.g. FTP_SOURCES) by adding a
    // single entry — the other entries' values are never shown or re-typed.
    const maps = s.maps || {};
    secBox.append(el("div", { class: "mt small", style: "font-weight:600" }, "Multi-entry secrets (JSON maps)"));
    const mapKeys = Object.keys(maps);
    if (mapKeys.length) {
      for (const k of mapKeys) {
        const chips = maps[k].map((name) => {
          const x = el("button", { class: "btn small danger", title: `delete ${name}` }, `${name} ✕`);
          x.onclick = async () => {
            if (!(await confirmDialog(`Delete entry “${name}” from ${k}?`, { confirmLabel: "delete" }))) return;
            try { await api(`/api/settings/secrets/${encodeURIComponent(k)}/entry/${encodeURIComponent(name)}`, { method: "DELETE" }); fill(); }
            catch (err) { toast(err.message, 4000, { error: true }); }
          };
          return x;
        });
        secBox.append(el("div", { class: "row", style: "margin:4px 0;flex-wrap:wrap", "data-map": k },
          el("span", { class: "mono small", style: "min-width:130px" }, k), ...chips));
      }
    } else {
      secBox.append(el("div", { class: "muted small" }, "none yet — add an entry below to start one (e.g. FTP_SOURCES)"));
    }
    const mKey = el("input", { type: "text", placeholder: "secret (e.g. FTP_SOURCES)", style: "flex:1", list: "secret-names", "data-map-entry": "key" });
    const mName = el("input", { type: "text", placeholder: "entry name (e.g. grantsforbina)", style: "flex:1", "data-map-entry": "name" });
    const mVal = el("textarea", { placeholder: '{"host": "…", "user": "…", "pass": "…"}', rows: "3", style: "width:100%;font-size:12px", "data-map-entry": "value" });
    const mSave = el("button", { class: "btn small primary" }, "add / replace entry");
    mSave.onclick = async () => {
      const key = mKey.value.trim(), name = mName.value.trim();
      if (!key || !name) { toast("enter a secret and an entry name"); return; }
      let value;
      try { value = JSON.parse(mVal.value); }
      catch { toast("the entry value must be valid JSON", 4000, { error: true }); return; }
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        toast('the entry value must be a JSON object, e.g. {"host": …}', 4000, { error: true }); return;
      }
      try {
        await api(`/api/settings/secrets/${encodeURIComponent(key)}/entry`, { method: "PUT", body: { name, value } });
        toast(`${key} · ${name} saved`); mName.value = ""; mVal.value = ""; fill();
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    secBox.append(
      el("datalist", { id: "secret-names" }, ...(s.needed || []).map((n) => el("option", { value: n.key }))),
      el("div", { class: "muted small mt" }, "Add or replace ONE entry of a JSON-map secret — the other entries stay untouched and their values are never shown."),
      el("div", { class: "row mt" }, mKey, mName),
      el("div", { class: "mt" }, mVal),
      el("div", { class: "row mt" }, mSave));
  }
  return fill();
}
