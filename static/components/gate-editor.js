// The RUN GATE editor: the checks that decide whether a scheduled fire becomes a run at all.
//
// A gate is a list of CHECKS (`run_gate.checks`), each one a known question with parameters —
// "is there unread mail from these senders?", "did this page change?" — rendered here from the
// vocabulary the server publishes (GET /api/gate/kinds), so a kind cannot exist on one side
// only. The routine's own `scripts/admit.py` is one more check (kind `script`), edited in place
// below its row, not the only way to have a gate.
//
// The editor edits a VALUE, never the server: every change goes to `onChange`; the page's one
// "accept changes" saves it with everything else. The exception is "Test the gate now",
// which asks the server what a fire WOULD do with the gate as shown — saved or not — through
// the real admission path and starts no run.

import { api } from "/static/api.js";
import { el, toastError } from "/static/util.js";

const ADMIT = "scripts/admit.py";

// What every gate does before its checks are asked — said once, where the checks are listed,
// because a person deciding whether a gate is safe needs to know what it can never skip.
const ALWAYS_RUNS = "A fire always becomes a run when a message or an answer is waiting, when "
  + "the last run did not finish ok, or when the recipe or settings changed since then. "
  + "Otherwise the fire is skipped only if EVERY check knows there is nothing to do — a check "
  + "that cannot tell (a mailbox that refuses the login, a page that times out) counts as work.";

const ADMIT_TEMPLATE = `"""Admission check for this routine: is there work for this fire?

net: none
secrets: (none)
fs: none
"""
import json
import sys

ctx = json.loads(sys.argv[1])     # {version, routine, run_id, reason}
work = True                       # decide here — and when unsure, say run
print(json.dumps({"version": 1, "decision": "run" if work else "skip",
                  "reason": "why, in one sentence"}))
`;

let kindsCache = null;
async function loadKinds() {
  kindsCache ??= api("/api/gate/kinds").then((d) => d.kinds);
  return kindsCache;
}

/**
 * gateEditor({ slug, value, onChange }) → { node, set(value) }
 * `value` is the routine's run_gate: { enabled, timeout_s, checks: [...] }.
 */
export function gateEditor({ slug, value, onChange }) {
  let gate = normalize(value);
  let kinds = null;
  const node = el("div", { class: "gate-editor", "data-gate-editor": "" });
  const result = el("div", { class: "gate-result", hidden: true });

  loadKinds().then((k) => { kinds = k; render(); })
    .catch((err) => { node.replaceChildren(el("div", { class: "muted small" },
      "could not load the gate's check vocabulary — ", String(err.message || err))); });
  node.replaceChildren(el("div", { class: "muted small" }, "loading the gate's checks…"));

  function change() { onChange?.(structuredClone(gate)); render(); }

  function render() {
    if (!kinds) return;
    const enabled = el("input", { type: "checkbox", "data-gate-enabled": "",
      checked: gate.enabled || null,
      disabled: gate.checks.length ? null : "",
      onchange: () => { gate.enabled = enabled.checked; change(); } });
    const timeout = el("input", { type: "number", min: "1", max: "300", "data-nopersist": true,
      value: String(gate.timeout_s), style: "width:76px", "data-gate-timeout": "",
      onchange: () => { gate.timeout_s = clamp(Number(timeout.value) || 30); change(); } });
    const add = el("select", { "data-gate-add": "", "data-nopersist": true,
      onchange: () => {
        if (!add.value) return;
        gate.checks.push(blankCheck(add.value, gate.checks));
        if (gate.checks.length === 1) gate.enabled = true;   // the first check switches it on
        change();
      } },
      el("option", { value: "" }, "+ add a check…"),
      ...Object.entries(kinds)
        .filter(([kind]) => kind !== "script" || !gate.checks.some((c) => c.kind === "script"))
        .map(([kind, spec]) => el("option", { value: kind }, `${label(kind)} — ${spec.meaning}`)));
    node.replaceChildren(
      el("label", { class: "row", style: "gap:8px" }, enabled,
        el("span", {}, "gate scheduled fires — start a run only when a check finds work")),
      el("div", { class: "faint small gate-explain" }, ALWAYS_RUNS),
      gate.checks.length
        ? el("div", { class: "gate-checks" }, ...gate.checks.map((c, i) => checkRow(c, i)))
        : el("div", { class: "muted small mt" },
            "no checks — every scheduled fire starts a run. Add the check that says whether "
            + "there is work, then tick the box."),
      el("div", { class: "row mt", style: "gap:10px;flex-wrap:wrap;align-items:center" },
        add,
        el("label", { class: "row", style: "gap:6px;align-items:center" },
          el("span", { class: "faint small" }, "answer within"), timeout,
          el("span", { class: "faint small" }, "seconds")),
        el("button", { class: "btn", type: "button", "data-gate-test": "",
          disabled: gate.checks.length ? null : "", onclick: test },
          "Test the gate now")),
      result);
  }

  function checkRow(check, index) {
    const spec = kinds[check.kind] || { meaning: "unknown check", params: {} };
    const params = Object.entries(spec.params).map(([name, p]) => field(check, name, p));
    return el("div", { class: "gate-check panel", "data-gate-check": check.kind },
      el("div", { class: "row spread", style: "gap:8px" },
        el("div", {},
          el("span", { class: "ref-tag" }, label(check.kind)),
          el("span", { class: "muted small", style: "margin-left:8px" }, spec.meaning)),
        el("button", { class: "btn small danger", type: "button",
          onclick: () => {
            gate.checks.splice(index, 1);
            if (!gate.checks.length) gate.enabled = false;
            change();
          } }, "remove")),
      params.length ? el("div", { class: "gate-params" }, ...params) : null,
      check.kind === "script" ? scriptPanel() : null);
  }

  function field(check, name, p) {
    const current = check[name];
    const set = (v) => {
      if (v === "" || v === null || (Array.isArray(v) && !v.length)) delete check[name];
      else check[name] = v;
      onChange?.(structuredClone(gate));
    };
    let input;
    if (p.type === "bool") {
      input = el("input", { type: "checkbox", checked: current ? "" : null,
        onchange: () => set(input.checked || null) });
    } else if (p.type === "int") {
      input = el("input", { type: "number", value: current ?? "", style: "width:90px",
        "data-nopersist": true,
        onchange: () => set(input.value === "" ? null : Number(input.value)) });
    } else if (p.type === "list") {
      input = el("input", { type: "text", value: (current || []).join(", "), "data-nopersist": true,
        placeholder: "comma-separated",
        onchange: () => set(input.value.split(",").map((s) => s.trim()).filter(Boolean)
          .map((s) => (name === "days" ? Number(s) : s))) });
    } else {
      input = el("input", { type: "text", value: current ?? "", "data-nopersist": true,
        placeholder: p.type === "secret" ? "SECRET_NAME" : "",
        onchange: () => set(input.value.trim()) });
    }
    return el("label", { class: "gate-param" },
      el("span", { class: "small" }, name.replaceAll("_", " "),
        p.required ? el("span", { class: "gate-required", title: "required" }, " *") : null),
      input,
      el("span", { class: "faint small" }, p.help));
  }

  function scriptPanel() {
    const box = el("div", { class: "gate-script" });
    const open = el("button", { class: "btn small", type: "button",
      onclick: async () => {
        let content = ADMIT_TEMPLATE;
        try {
          content = (await api(`/api/routines/${slug}/file?path=${encodeURIComponent(ADMIT)}`))
            .content;
        } catch { /* no script yet — start from the template */ }
        const area = el("textarea", { class: "code", rows: "16", spellcheck: "false",
          "data-nopersist": true });
        area.value = content;
        box.replaceChildren(area, el("div", { class: "row mt", style: "gap:8px" },
          el("button", { class: "btn primary small", type: "button", onclick: async () => {
            try {
              await api(`/api/routines/${slug}/file`, { method: "PUT",
                body: { path: ADMIT, content: area.value } });
              box.replaceChildren(open, el("span", { class: "muted small" },
                ` saved ${ADMIT} — test the gate to see its answer`));
            } catch (err) { toastError(err); }
          } }, `save ${ADMIT}`),
          el("button", { class: "btn small", type: "button",
            onclick: () => box.replaceChildren(open) }, "close")));
      } }, `edit ${ADMIT}`);
    box.replaceChildren(open, el("span", { class: "faint small", style: "margin-left:8px" },
      "the routine's own predicate: it receives the fire as JSON and prints ",
      el("code", {}, '{"version": 1, "decision": "run"|"skip", "reason": "…"}'),
      ". It runs in the routine's sandbox with only what its header declares; a script "
      + "that fails is recorded as a failed fire, never a skip. Saving the script is "
      + "immediate — it is a file of the routine, not a setting."));
    return box;
  }

  async function test() {
    result.hidden = false;
    result.replaceChildren(el("div", { class: "muted small" }, "asking the checks…"));
    try {
      const r = await api(`/api/routines/${slug}/gate/test`, { method: "POST",
        body: { run_gate: gate } });
      const verdict = { run: "a fire now would START a run", skip: "a fire now would be SKIPPED",
        error: "a fire now would FAIL" }[r.decision] || r.decision;
      // el() drops a null child; replaceChildren would print it — so the optional stderr block
      // rides a filtered list
      result.replaceChildren(...[
        el("div", { class: `gate-verdict gate-${r.decision}`, "data-gate-verdict": r.decision },
          verdict),
        el("div", { class: "small" }, r.reason),
        ...(r.checks || []).map((c) => el("div", { class: "small gate-check-result" },
          el("span", { class: "ref-tag" }, label(c.kind)), " ",
          c.work ? "work — " : "nothing to do — ", c.reason)),
        r.stderr ? el("pre", { class: "small gate-stderr" }, r.stderr) : null,
      ].filter(Boolean));
    } catch (err) {
      result.replaceChildren(el("div", { class: "gate-verdict gate-error", "data-gate-verdict": "error" },
        "the test could not run"), el("div", { class: "small" }, String(err.message || err)));
    }
  }

  return {
    node,
    set(next) { gate = normalize(next); render(); },
  };
}

function normalize(value) {
  const v = structuredClone(value || {});
  return { enabled: !!v.enabled, timeout_s: clamp(Number(v.timeout_s) || 30),
           checks: Array.isArray(v.checks) ? v.checks : [] };
}

function clamp(n) { return Math.min(300, Math.max(1, Math.round(n))); }

function blankCheck(kind, existing) {
  const used = new Set(existing.map((c) => c.id));
  let n = existing.length + 1;
  while (used.has(`c${n}`)) n += 1;
  return { kind, id: `c${n}` };
}

function label(kind) { return kind.replaceAll("_", " "); }
