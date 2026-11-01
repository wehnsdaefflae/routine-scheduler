// The run rail's SCRIPTS section: which persistent helpers the routine running here holds.
//
// Operator order 2026-11-01: "a routine's scripts should have a section in the right routine run
// sidebar". A routine's `scripts/` are the tooling its runs call every fire, and until now the
// only way to see what a run could call was to open the recipe browser and list a directory —
// while the run page, which is where a run is actually watched, named none of them.
//
// It reads `GET /api/routines/{slug}/scripts`, whose rows come from `rsched/scripts.py`'s
// `list_scripts` — the SAME catalog the engine puts in the run's own capabilities digest. So the
// rail shows what the run was told it could call, and a second description cannot drift from it.
//
// ONE read, no poller: a routine's script set is a property of the recipe, not of the run, and
// the recipe cannot change while a run is active (the console queues such edits to run end). The
// section is ADDED only when the read finds a script, like the jobs and oversight sections —
// an always-present, always-empty rail card teaches a reader to skip the section.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

// `parse_header` keeps the docstring's whole first line, so a summary arrives as
// "tidy-up — delete the temp files…". The name is already the row's heading; repeating it in the
// line below is noise in a narrow rail. Stripping is the CONSOLE's business — the route serves
// the header as the run is shown it.
//
// The lead is matched LOOSELY on purpose, and a red gate is what taught it: a script's FILENAME
// is snake_case (`tidy_up.py`) while the docstring header it carries is conventionally
// kebab-case (`tidy-up — …`), and both spellings are live in this instance's routines. A strict
// name match left the redundant lead on exactly those rows.
function blurb(row) {
  const s = String(row.summary || "").trim();
  const loose = row.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/[-_]/g, "[-_]");
  const lead = new RegExp(`^${loose}\\s*[—–:-]\\s*`, "i");
  return s.replace(lead, "");
}

function scriptNode(row) {
  const kids = [el("div", { class: "rj-title" }, el("b", {}, row.name))];
  const line = blurb(row);
  // Full text on hover: a rail is narrow and a usage block is several lines. The `title` is
  // the usage when there is one, because that is what a reader wants before calling it.
  if (line) kids.push(el("div", { class: "faint small prose", title: row.usage || line }, line));
  else if (row.usage) kids.push(el("div", { class: "faint small mono", title: row.usage }, "usage"));
  return el("div", { "data-run-script": row.name }, ...kids);
}

/**
 * Mount the rail's scripts section. `rail` is the shared rail component and `slug` the routine
 * whose run this is. Returns `{ stop }`; nothing is added to the rail until the read finds a
 * script, and a failed read adds nothing at all — a conversation's run or a detached task has no
 * routine behind its id and 404s here, which is not a condition worth a card.
 */
export function mountRunScripts(rail, { slug }) {
  let stopped = false;
  const host = el("div", { class: "runjob" });

  (async () => {
    let data;
    try {
      data = await api(`/api/routines/${encodeURIComponent(slug)}/scripts`);
    } catch {
      return;
    }
    const rows = (data && data.scripts) || [];
    if (stopped || !rows.length) return;
    host.replaceChildren(...rows.map(scriptNode));
    rail.add("scripts", host);
  })();

  return { stop() { stopped = true; } };
}
