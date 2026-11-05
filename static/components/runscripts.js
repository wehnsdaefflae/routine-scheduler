// The rail's SCRIPTS section: which persistent helpers the routine — or conversation — holds.
//
// Operator order 2026-11-01: "a routine's scripts should have a section in the right routine run
// sidebar". A routine's `scripts/` are the tooling its runs call every fire, and until now the
// only way to see what a run could call was to open the recipe browser and list a directory —
// while the run page, which is where a run is actually watched, named none of them.
//
// F833 (operator, 2026-11-02: "why do conversations not show the routine's scripts in the right
// side bar? routine runs seem to do it"; decided 2026-11-05: conversations hold scripts). The
// same section mounts in the run page's rail (components/runrail.js, either home) and in the
// conversation view's rail, reading `GET /api/{routines|conversations}/{slug}/scripts` — rows
// from `rsched/scripts.py`'s `list_scripts`, the SAME catalog the engine puts in the run's own
// capabilities digest, so the rail shows what the run was told it could call.
//
// ONE read per mount, no poller: a routine's script set is a property of its recipe, which the
// console does not change under a live run. A conversation WRITES its own scripts mid-reply, so
// its view calls `refresh()` when a reply ends, as it does for the artifacts. The section is
// ADDED only once a read finds a script, like the jobs and oversight sections — an
// always-present, always-empty rail card teaches a reader to skip the section.

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

// Show the section with `nodes`, adding it on first use — or hide it when there is nothing.
function show(rail, host, nodes) {
  host.replaceChildren(...nodes);
  if (rail.has("scripts")) rail.toggle("scripts", nodes.length > 0);
  else if (nodes.length) rail.add("scripts", host);
}

/**
 * Mount the rail's scripts section. `rail` is the shared rail component, `slug` the routine or
 * conversation, `base` its home's route prefix (`routines` | `conversations`). Returns
 * `{ stop, refresh }`. Every home that holds scripts has the route, so a failed read is a real
 * failure and says so in the section rather than vanishing.
 */
export function mountRunScripts(rail, { slug, base = "routines" }) {
  let stopped = false;
  const host = el("div", { class: "runjob" });

  async function load() {
    let rows;
    try {
      rows = (await api(`/api/${base}/${encodeURIComponent(slug)}/scripts`))?.scripts || [];
    } catch (err) {
      if (!stopped) show(rail, host, [el("div", { class: "faint small" },
        `couldn't list scripts — ${err.message}`)]);
      return;
    }
    if (!stopped) show(rail, host, rows.map(scriptNode));
  }
  load();

  return { stop() { stopped = true; }, refresh: load };
}
