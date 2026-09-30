// The routine page's SETTINGS FORM — one draft over every setting, one accept.
//
// The page reads ONE document (GET /api/routines/<slug>/settings), every control edits this
// draft instead of the server, a changed control says so where it is — and in the collapsed
// group it hides in — and a single "accept changes" sends every change the person kept
// (POST …/settings), which the server routes to the writer that owns each field.
//
// Three values per field; the page shows the relation between them:
//   saved    — what the routine holds now;
//   draft    — what it WILL hold if the person accepts (starts as saved, overlaid with the
//              server's pending proposal when there is one — "check the changes i recommend.");
//   pattern  — what the routine's pattern carries, for the fields it governs. A saved value
//              that differs from it is an OVERRIDE; a draft value that differs from saved is a
//              CHANGE. Those are different facts; the page marks them differently.
//
// Values are compared in their CANONICAL form, which `canonical` below computes exactly the way
// the server's `patterns/fields.py` does — a list of rules is a set, a trigger's id and token
// are identity rather than configuration, a capability left empty is the same as one left out,
// a finish-line outcome is what the operator set and never what a run reported about it. The
// two sides must agree, or the page marks a change the server then drops as noise (and the
// accept button offers to "save" nothing).

import { api } from "/static/api.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const OUTCOME_ID_RE = /^g\d+$/;
const JUDGES = ["run", "you", "date"];
const TRIGGER_IDENTITY = new Set(["id", "token", "created"]);

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const jsonOf = (v) => JSON.stringify(v);

export function sortKeys(v) {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (isObj(v)) return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortKeys(v[k])]));
  return v;
}

const asSet = (v) => [...new Set(v.map(String))].sort();

function finishLine(doc) {
  const outcomes = [];
  for (const o of Array.isArray(doc.outcomes) ? doc.outcomes : []) {
    if (!isObj(o) || !String(o.text ?? "").trim()) continue;
    const judge = JUDGES.includes(o.judge) ? o.judge : "you";
    const date = String(o.date || "");
    outcomes.push({
      date: judge === "date" && DATE_RE.test(date) ? date : "",
      id: OUTCOME_ID_RE.test(String(o.id || "")) ? String(o.id) : "",
      judge,
      status: o.status === "met" ? "met" : "open",
      text: String(o.text).trim(),
    });
  }
  const until = String(doc.until || "");
  return { outcomes, until: DATE_RE.test(until) ? until : "" };
}

/** The comparable form of one field's value — `patterns/fields.canonical`, in the browser. */
export function canonical(shape, key, value) {
  if (value === null || value === undefined) {
    return shape === "set" ? [] : shape === "map" ? {} : null;
  }
  if (shape === "set") return Array.isArray(value) ? asSet(value) : [];
  if (key === "capabilities") {
    const caps = isObj(value) ? value : {};
    return Object.fromEntries(Object.keys(caps).sort()
      .filter((k) => !(caps[k] === null || caps[k] === undefined || caps[k] === ""
                       || (Array.isArray(caps[k]) && !caps[k].length)))
      .map((k) => [k, Array.isArray(caps[k]) ? asSet(caps[k]) : caps[k]]));
  }
  if (key === "triggers") {
    const rows = (Array.isArray(value) ? value : []).filter(isObj)
      .map((t) => sortKeys(Object.fromEntries(
        Object.entries(t).filter(([k]) => !TRIGGER_IDENTITY.has(k)))));
    return rows.sort((a, b) => (jsonOf(a) < jsonOf(b) ? -1 : jsonOf(a) > jsonOf(b) ? 1 : 0));
  }
  if (key === "finish_line") return finishLine(isObj(value) ? value : {});
  if (shape === "map" || shape === "doc") return sortKeys(value);
  return value;
}

export function createSettingsForm(slug, payload) {
  let data = null;
  let meta = {};
  let draft = {};
  let reasons = {};
  let patternChoice = null;          // a pending pattern switch (a slug, "" for none), or null
  const listeners = new Set();

  const canon = (key, value) => jsonOf(canonical(meta[key]?.shape, key, value));
  const emit = () => { for (const fn of listeners) fn(form); };

  /** Replace everything with `next`: the saved values, overlaid with its pending proposal. */
  function reset(next) {
    data = next;
    meta = Object.fromEntries((data.meta || []).map((m) => [m.key, m]));
    draft = structuredClone(data.fields || {});
    reasons = {};
    patternChoice = null;
    const pending = data.draft;
    if (pending) {
      for (const [key, row] of Object.entries(pending.changes || {})) {
        if (!(key in meta)) continue;
        draft[key] = structuredClone(row.value);
        if (row.reason) reasons[key] = row.reason;
      }
      patternChoice = pending.pattern ?? null;
    }
    emit();
  }

  const governed = (key) => !!data.pattern?.settings && key in data.pattern.settings
    // a lane-managed routine's clock is the lane's: its own schedule can depart from nothing
    && !(key === "schedule" && data.lane_managed);

  const form = {
    slug,
    get payload() { return data; },
    keys: () => Object.keys(meta),
    meta: (key) => meta[key],
    canon,
    saved: (key) => data.fields?.[key],
    get: (key) => draft[key],
    set(key, value) { form.setMany({ [key]: value }); },
    /** Several keys edited as ONE change (a conduct doc and the capabilities it switches on),
     *  so every reader repaints once, over a draft that is never half-applied. */
    setMany(values) {
      for (const [key, value] of Object.entries(values)) {
        draft[key] = structuredClone(value);
        if (!form.changed(key)) delete reasons[key];
      }
      emit();
    },
    reason: (key) => reasons[key] || "",
    changed: (key) => canon(key, draft[key]) !== canon(key, data.fields?.[key]),
    /** Keys whose draft differs from the saved value. */
    changes() { return Object.keys(meta).filter((k) => form.changed(k)); },
    governed,
    get pattern() { return data.pattern || null; },
    patternValue: (key) => data.pattern?.settings?.[key],
    /** The SAVED value departs from the pattern — the server's own list is the authority. */
    override: (key) => (data.overrides || []).includes(key),
    /** Would the DRAFT value depart from the pattern once accepted? */
    draftOverride: (key) => governed(key)
      && canon(key, draft[key]) !== canon(key, data.pattern.settings[key]),
    get pendingPattern() { return patternChoice; },
    get proposal() { return data.draft || null; },
    patternSwitch: () => patternChoice !== null && patternChoice !== (data.pattern?.slug || ""),
    count() { return form.changes().length + (form.patternSwitch() ? 1 : 0); },
    /** Put the saved value back — for one key or a list of keys edited together. */
    revert(keys) {
      for (const key of Array.isArray(keys) ? keys : [keys]) {
        draft[key] = structuredClone(data.fields?.[key]);
        delete reasons[key];
      }
      emit();
    },
    /** Set the draft to the pattern's value — for one key or a list of keys. */
    toPattern(keys) {
      const values = Object.fromEntries((Array.isArray(keys) ? keys : [keys])
        .filter(governed).map((k) => [k, data.pattern.settings[k]]));
      if (Object.keys(values).length) form.setMany(values);
    },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },

    /** Every change back to the saved value. A pending proposal is withdrawn on the server as
     *  well — otherwise the next visit would lay it over the page again. */
    async discardAll() {
      if (data.draft) await api(`/api/routines/${slug}/settings/draft`, { method: "DELETE" });
      reset({ ...data, draft: null });
    },
    /** Withdraw the pending proposal. A field the proposal set and the person has not touched
     *  since goes back to its saved value; an edit the person made themselves stays. */
    async discardProposal() {
      const proposal = data.draft;
      await api(`/api/routines/${slug}/settings/draft`, { method: "DELETE" });
      const kept = structuredClone(draft);
      for (const [key, row] of Object.entries(proposal?.changes || {})) {
        if (key in meta && canon(key, kept[key]) === canon(key, row.value)) {
          kept[key] = structuredClone(data.fields?.[key]);
        }
      }
      data = { ...data, draft: null };
      draft = kept;
      reasons = {};
      patternChoice = null;
      emit();
    },
    async accept() {
      const changes = Object.fromEntries(form.changes().map((k) => [k, draft[k]]));
      const body = { changes };
      if (form.patternSwitch()) body.pattern = patternChoice;
      const next = await api(`/api/routines/${slug}/settings`, { method: "POST", body });
      reset(next);
      return next;
    },
    /** Propose following another pattern — the server writes it as a pending proposal. */
    async follow(pattern) {
      reset(await api(`/api/routines/${slug}/settings/follow`,
        { method: "POST", body: { pattern } }));
    },
    /** "Recommend for this routine": one slow model call; lands as a pending proposal. */
    async recommend() {
      reset(await api(`/api/routines/${slug}/settings/recommend`, { method: "POST" }));
    },
    /** "Save as new pattern": the routine's SAVED values become a library pattern it follows. */
    async saveAsPattern(body) {
      const next = await api(`/api/routines/${slug}/patterns`, { method: "POST", body });
      reset(next);
      return next;
    },
    /** A fresh read after a change that did not go through this form (a grant decided on the
     *  Decisions page, a cron cleared by its own control): the saved values move under the
     *  draft; every field the person has not edited moves with them. */
    rebase(next) {
      const before = data;
      const edited = new Set(form.changes());
      data = next;
      meta = Object.fromEntries((data.meta || []).map((m) => [m.key, m]));
      for (const key of Object.keys(meta)) {
        if (!edited.has(key)) draft[key] = structuredClone(data.fields?.[key]);
      }
      if (!before.draft && data.draft) {
        for (const [key, row] of Object.entries(data.draft.changes || {})) {
          if (edited.has(key) || !(key in meta)) continue;
          draft[key] = structuredClone(row.value);
          if (row.reason) reasons[key] = row.reason;
        }
        patternChoice = data.draft.pattern ?? patternChoice;
      }
      emit();
    },
    reload(next) { reset(next); },
  };
  reset(payload);
  return form;
}

export async function loadSettings(slug) {
  return api(`/api/routines/${slug}/settings`);
}
