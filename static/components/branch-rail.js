// The conversation rail's BRANCHES section: the parent this conversation was forked from, and
// each branch forked off it — live.
//
// D306 point 2. The operator kept conversation branches and diagnosed their non-use as the
// console's fault — "unused only because the UI is bad" — and this is the point that names what
// was missing: branches were "a lineage LINE in the header: names joined by commas, no state, no
// way to tell a branch working from one long finished". A comma-joined list of names tells a
// reader nothing he can act on.
//
// So a row carries what became of the branch: its state as the same `chip(state)` the background
// section uses, the turn it was forked at, when it last moved, and "handed back" once it has. The
// section appears ONLY when there is a parent or a branch, which is the overwhelming minority of
// conversations — the rail of a root conversation with no family is unchanged.
//
// REFRESH IS EVENT-DRIVEN, never polled, for the reason bg-tasks.js states for its own rows: the
// daemon publishes `run_started` / `run_finished` for a branch's run exactly as for any other, so
// the section reads once when something actually changed. There is no timer at all here — unlike
// a detached task, a branch's row shows no elapsed time that has to tick.

import { chip, el, when } from "/static/util.js";

/** True when `runId` belongs to this conversation's family — the parent or one of the branches.
 *
 * A run id is `<slug>:<ts>` (ids.run_id). The slugs come from the lineage already read, which is
 * exactly right here and not in the background section's case: a branch cannot appear without a
 * fork, and a fork is a request this page made, so there is no "a run of something I have never
 * heard of" case to catch.
 */
export function ownsRun(runId, slugs) {
  const slug = String(runId || "").split(":")[0];
  return Boolean(slug) && slugs.includes(slug);
}

/** One row: the conversation's name as a link, its state chip, and what became of it. */
function row(entry, { role }) {
  const node = el("div", { class: "branch-row", "data-branch": entry.slug });
  const name = el("a", { href: `#/conversations/${entry.slug}`, class: "branch-name" },
                  entry.name || entry.slug);
  // The state chip is the SAME vocabulary every other surface uses, so a branch that is running
  // reads like a run that is running — a second set of words for one fact is how two surfaces
  // come to disagree about it.
  const bits = [el("span", { class: "branch-role faint small" }, role),
                name,
                entry.state ? chip(entry.state, entry.state) : null];
  if (Number.isInteger(entry.turn)) {
    bits.push(el("span", { class: "faint small", title: "the turn it was forked at" },
                 `at turn ${entry.turn}`));
  }
  if (entry.updated) bits.push(el("span", { class: "faint small" }, when(entry.updated)));
  if (entry.handed_back) {
    bits.push(chip("handed back", "ok"));
  }
  node.append(...bits.filter(Boolean));
  return node;
}

/** The branches section for one conversation, as `{ node, paint, slugs, ownsRun }`.
 *
 * IT MAKES NO REQUEST OF ITS OWN, and that is the design rather than a convenience.
 * `branchControls` (components/branches.js) already reads `/lineage` exactly once per actual
 * family CHANGE — it holds a family-signature guard so the conversation list's own 20 s reload
 * cannot turn that read into a poll, and point 5 of this same order made the route conditional so
 * a conversation with no family never calls it at all. A section fetching the same answer a second
 * time would undo both. So the header's read FEEDS this: `paint(lineage)` is called with what it
 * got, and `slugs()`/`ownsRun()` answer which runs belong to this family so the host knows
 * whether a bus event is worth asking for a fresh one.
 */
export function branchRail() {
  const node = el("div", { class: "branch-rail" });
  let family = [];

  const paint = (lineage) => {
    const rows = [];
    const parent = lineage?.parent;
    if (parent?.slug) {
      rows.push(parent.exists
        ? row({ ...parent, state: parent.state, updated: parent.updated }, { role: "from" })
        // A deleted parent leaves its record standing: the branch's history still came from
        // somewhere, and saying so beats reading as a root conversation.
        : el("div", { class: "branch-row" },
             el("span", { class: "branch-role faint small" }, "from"),
             el("span", { class: "branch-name faint" }, parent.name || parent.slug),
             chip("deleted", "bad")));
    }
    for (const kid of lineage?.branches || []) rows.push(row(kid, { role: "branch" }));
    family = [parent?.slug, ...(lineage?.branches || []).map((k) => k.slug)].filter(Boolean);
    node.replaceChildren(...rows);
    return rows.length;
  };

  return { node, paint, slugs: () => family, ownsRun: (id) => ownsRun(id, family) };
}

/** A body for the BRANCHES section, to hand to the header's branch bar before the rail exists.
 *
 * Two calls rather than one because of the view's own order: the header is rendered first (and it
 * is the header's lineage read that FILLS this), and the rail is built afterwards.
 */
export function branchSectionBody() {
  return el("div", { class: "branch-rail-body" });
}

/** Put `body` on the rail as the BRANCHES section, shown only when there IS a family.
 *
 * The section's place and its existence in one call, because that is one decision and the view it
 * is mounted from (`views/conversations.js`) is a frozen file at its ratchet ceiling. `family` is
 * the caller's own cheap answer off the conversation list rows (`branches.familyOf`): no parent
 * and no branch means no section, which is the overwhelming majority of conversations and the
 * reason point 5 made the lineage read conditional in the first place.
 */
export function mountBranchSection(rail, body, family) {
  rail.add("branches", body);
  rail.toggle("branches", Boolean(family?.parent || family?.branches));
}
