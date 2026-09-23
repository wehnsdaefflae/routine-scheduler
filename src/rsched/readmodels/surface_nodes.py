"""The setup surface's NODE VOCABULARY — what a row is, what an unmet one costs, and how
two rows about one entity merge.

Split out of `surface.py` when it reached 663 lines. The four joins that emit rows — the
declared needs (`surface_needs`), the schedule (`surface_schedule`), the capability coverage
(`surface_caps`) and `surface.routine_surface` itself — all speak this vocabulary, so it is
the one piece none of them may own. Nothing here reads a routine or a library: it is the
shape of a row and the rule for keeping one.
"""

from __future__ import annotations

from pathlib import Path

# What an unmet need COSTS, worst first. The vocabulary is deliberately about consequence, not
# about severity in the abstract: the operator's question is "will this break, or interrupt, or
# is it just worth knowing?" — every row has to answer that.
BLOCKS = "blocks"           # the call is rejected or fails — the run cannot do the thing
INTERRUPTS = "interrupts"   # the run stops mid-way to ask, spending a turn and your attention
NOTE = "note"               # worth knowing; nothing is broken
OK = "ok"
_ORDER = {BLOCKS: 0, INTERRUPTS: 1, NOTE: 2, OK: 3}

# WHERE a drop is performed, which is not one place for every row. A routine's own save FLOORS
# its mapping, so a capability it holds itself is dropped on its own page. A domain's shared
# block is deliberately not floored — a member may hold the covering doc — and a member's list
# UNIONS with the domain's at every load, so a capability the domain supplies is restored the
# moment the member drops it: the only surface that can drop that one is the domain's editor.
# The `fix` carries which, because an offer that travels to a control unable to perform it
# spends the reader's trust as well as their time.
OWNER_ROUTINE = "routine"
OWNER_DOMAIN = "domain"


def _covered(path: Path, roots: list[Path]) -> bool:
    """Is `path` inside (or equal to) one of `roots`? The same containment the sandbox uses."""
    return any(path == root or root in path.parents for root in roots)


def _node(eid: str, state: str, severity: str, why: str, effect: str = "",
          fix: dict | None = None, source: dict | None = None) -> dict:
    """One typed need. `source` is machine-readable PROVENANCE — which conduct doc or which
    util put this row here — so a UI can group rows under the ability that owns them without
    parsing `why`. Joining on prose is exactly what invariant 5 forbids.

    `fix` is the same idea pointed FORWARDS: a machine-readable REMEDY, so a caller can offer
    the action without parsing `effect`. Its vocabulary is a `kind` plus the parameters that
    kind needs — `{"kind": "add_secret", "name": "FOO_TOKEN"}` — and it names WHAT has to
    happen, never where a UI puts it: `rsched validate` renders these same nodes on a terminal
    with no sections to scroll to and turns each kind into words through `remedies.py`.

    A kind is REGISTERED in five places, of which two are held by a gate:

    - here, the only one that emits it;
    - `readmodels/remedies.py` — the `REMEDIES` table, the same kind in words for the two
      callers with no panel. GATED: `tests/test_surface.py` reads the kinds off this module's
      source and the table's keys together, so a kind with no words fails there;
    - `static/components/surface-view.js` — the `FIX` map, one kind to one panel. `section` is
      the `sec-<id>` anchor every config section heading carries (`components/settings-section.js`
      builds it); `focus` is a selector for the ONE control inside that panel: the ability
      card `abilities.js` stamps `data-ability="<slug>"`, or the orphan row it stamps
      `data-drop="<class>:<name>"`. A kind the map does not name renders no offer at all, which
      is why the console's vocabulary may not lag behind this one;
    - `static/components/setupcheck.js` — the strip above the hero, which renders through
      surface-view's `fixLine` rather than a second map;
    - `tests/ui/test_surface_fix.py` — the `CASES` table, the only registration that asks
      whether the panel a kind lands on can PERFORM the act. GATED:
      `test_no_fix_kind_reaches_the_console_without_a_case_here` holds the console map, the
      CLI wording and this table to one vocabulary.

    UNMET decides who carries one, which is not the same question as severity. Every unmet
    row carries a fix whatever it costs — a cron the file records and the lane overrides is as
    fixable as an absent secret, so both are worth an offer. Every row that is NOT unmet
    carries none. A met row would invite a click to check what is already true; a row reporting
    a deliberate switch (`action:write_recipe` "on") or a finished routine (`schedule:goal`
    "retired") would offer to UNDO it, which reads as a defect report on a routine that is
    exactly right. Two NOTE rows sitting in one table, one offering an action and one not, is
    how a reader learns that an absent fix means nothing in particular — so it means this.
    Where both readings land on ONE entity, `_one_row_per_entity` keeps the fix-less one.
    """
    return {"id": eid, "state": state, "severity": severity, "why": why,
            "effect": effect, "fix": fix or {}, "source": source or {}}


def _rank(node: dict) -> tuple[int, int]:
    """The merge order for two rows about one entity: worst severity first, then the row that
    offers an action LAST — see `_one_row_per_entity`.
    """
    return _ORDER[node["severity"]], 1 if node.get("fix") else 0


def _one_row_per_entity(nodes: list[dict]) -> list[dict]:
    """One row per entity, chosen by a STATED precedence rather than by the order the checks
    happened to run in.

    Two checks legitimately reach the same id: `action:write_recipe` is emitted once as the
    deliberate switch it is (no fix) and once as uncovered by any held doc (with one), both
    NOTE. Which of the two survives decides whether the panel offers to undo a routine set up
    exactly as intended — far too load-bearing to rest on which append comes first upstream,
    where nothing marks either line as the one that must not move.

    Worst severity wins. At equal severity the row with NO fix wins, which is the "not unmet ⇒
    no fix" rule of `_node` read at the merge: a fix-less row is by construction one reporting
    something that is not unmet. The same entity being unmet on another reading never makes an
    offer to undo the deliberate half correct.
    """
    best: dict[str, dict] = {}
    for n in nodes:
        prev = best.get(n["id"])
        if prev is None or _rank(n) < _rank(prev):
            best[n["id"]] = n
    return list(best.values())
