"""Rule ASSISTS — the curated half of the relevance-trigger layer.

A general rule is advisory prose the model must notice the moment for. Most of a rule's
length is that noticing: the "when" scaffolding it has to hold and match against the
situation in front of it. The realistic failure is not a run that refuses a rule — it is a
run that means to follow one and forgets it at the moment it applies.

An **assist** externalizes the noticing. It is a `(moment, predicate) -> line` declaration in
a rule's own frontmatter: a deterministic check over the SITUATION that surfaces the rule's
operative line exactly when the rule becomes relevant. The rule does not shrink; it FACTORS —
the trigger becomes machine-checked, the operative line becomes the surfaced payload, and the
full rationale stays where it was, read on demand with `read_rule`.

Why this is the tractable half. A mechanical check of COMPLIANCE is impossible for most
rules: a compliant run and a violating one can leave byte-identical traces, differing only in
the reasoning that produced them, and reasoning is never in the trace. `root-cause-fix` is the
clean example — whether a diff hit the cause or the symptom is a fact about future inputs, not
about anything observable now. But RELEVANCE is a property of the situation, and the situation
IS in the trace. So the rule you can never check, you can still TIME.

Named `assists:` and not `triggers:` on purpose: routine.yaml already has a `triggers:` key
(rsched/triggers.py — the events that FIRE a run). These are a different concept that happens
to share the word, and one name per concept is worth more here than matching the design note.

This file is the LIBRARY side — what an assist IS, how a rule's block is validated, and how
the set is read back. The runtime that evaluates the predicates and delivers the payload is
`engine/assist.py`; the predicates themselves are `engine/assist_predicates.py`, because a
library document may declare a check by NAME but may never ship code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: The moments an assist can fire at, and what each one can reach.
#:
#: `observation` and `boundary` are FREE — they append to a message the run was getting
#: anyway, so they cost no turn. `pre-finish` costs one: it sets the finish aside so the
#: model can act on the line, which is the only way a pre-finish assist can matter at all.
#:
#: `pre-action` costs one too, and can only ever be a HOLD. At the point an action is chosen
#: but not executed, the only way to reach the model is to stop the action — "remind and let
#: it run" is not expressible there, because the action is already emitted. That is why the
#: moment and the payload are coupled rather than free to combine.
MOMENTS = ("pre-action", "observation", "boundary", "pre-finish")

#: The payload axis is `remind -> scaffold -> do -> hold`. The two ends exist; `scaffold` and
#: `do` do not, because each needs a helper channel that is not built — and shipping an enum
#: value the engine cannot honour would be a lie in the library's own schema.
PAYLOADS = ("remind", "hold")

#: moment -> the payloads it can carry. `pre-action` can ONLY hold (there is no way to reach a
#: chosen action without stopping it), and the free moments can only remind (a hold needs an
#: action to stop, and neither of them has one).
MOMENT_PAYLOADS = {"pre-action": ("hold",), "observation": ("remind",),
                   "boundary": ("remind",), "pre-finish": ("remind",)}

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_LINE_CHARS = 500
STATE_FILE = "assists.json"


@dataclass(frozen=True)
class Assist:
    """One declaration, resolved: which rule it belongs to and what it does when it fires."""

    rule: str
    id: str
    moment: str
    predicate: str
    payload: str
    line: str

    @property
    def key(self) -> str:
        """The stable identity of one assist — the per-run fire guard and the tally key."""
        return f"{self.rule}/{self.id}"


def normalize_assists(raw: object, *, rule: str = "", label: str = "assists",
                      ) -> tuple[list[Assist], list[str]]:
    """Validate + normalize one rule's `assists:` block. Returns (assists, problems).

    Invalid entries are DROPPED and reported rather than raised, the way `expects:` behaves:
    a bad edit degrades one assist instead of taking down every run that holds the rule. The
    linter turns the same problems into an authoring error, so a bad block never lands.
    """
    from .engine.assist_predicates import PREDICATES

    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [f"{label} must be a LIST of assist entries"]
    out: list[Assist] = []
    problems: list[str] = []
    seen: set[str] = set()
    for n, item in enumerate(raw, 1):
        where = f"{label}[{n}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: each entry must be a mapping")
            continue
        unknown = set(item) - {"id", "moment", "predicate", "payload", "line"}
        if unknown:
            problems.append(f"{where}: unknown key(s) {sorted(unknown)} — an assist is "
                            "id / moment / predicate / payload / line")
            continue
        aid = str(item.get("id") or "")
        if not ID_RE.match(aid):
            problems.append(f"{where}: 'id' must be a short kebab-case name, got {aid!r}")
            continue
        if aid in seen:
            problems.append(f"{where}: duplicate id {aid!r} in this rule")
            continue
        seen.add(aid)
        moment = str(item.get("moment") or "")
        if moment not in MOMENTS:
            problems.append(f"{where}: 'moment' must be one of {list(MOMENTS)}, got "
                            f"{moment!r}")
            continue
        predicate = str(item.get("predicate") or "")
        if predicate not in PREDICATES:
            problems.append(f"{where}: unknown predicate {predicate!r} — the engine resolves "
                            f"these by name and knows {sorted(PREDICATES)}; a library "
                            "document declares a check, it never ships one")
            continue
        if PREDICATES[predicate].moment != moment:
            problems.append(f"{where}: predicate {predicate!r} answers at the "
                            f"{PREDICATES[predicate].moment!r} moment, not {moment!r}")
            continue
        payload = str(item.get("payload") or "remind")
        if payload not in PAYLOADS:
            problems.append(f"{where}: 'payload' must be one of {list(PAYLOADS)} — the "
                            "scaffold/do rungs are not built yet")
            continue
        if payload not in MOMENT_PAYLOADS[moment]:
            problems.append(f"{where}: the {moment!r} moment carries "
                            f"{list(MOMENT_PAYLOADS[moment])}, not {payload!r} — a chosen "
                            "action can only be reached by STOPPING it, and a moment with no "
                            "action in hand has nothing to stop")
            continue
        line = " ".join(str(item.get("line") or "").split())
        if not line:
            problems.append(f"{where}: 'line' must carry the rule's operative instruction — "
                            "the text surfaced at the moment. Author it deliberately; it is "
                            "never auto-truncated from the rule body")
            continue
        if len(line) > MAX_LINE_CHARS:
            problems.append(f"{where}: 'line' is {len(line)} characters — at most "
                            f"{MAX_LINE_CHARS}. It is read at a moment the run is busy; the "
                            "full rationale stays in the rule, reachable with read_rule")
            continue
        out.append(Assist(rule=rule, id=aid, moment=moment, predicate=predicate,
                          payload=payload, line=line))
    return out, problems


def read_library_assists(rules_home: Path) -> list[Assist]:
    """Every assist declared by every rule in the library, in slug order.

    Lenient like `read_library_expects`: an unreadable or malformed rule contributes nothing
    rather than breaking a boot. The linter is where a bad block is a loud problem.
    """
    from .library_docs import parse_lenient

    home = Path(rules_home)
    if not home.is_dir():
        return []
    out: list[Assist] = []
    for path in sorted(home.glob("*.md")):
        try:
            meta = parse_lenient(path.read_text(encoding="utf-8"))[0]
        except OSError:
            continue
        assists, _ = normalize_assists(meta.get("assists"), rule=path.stem)
        out.extend(assists)
    return out


def for_rules(rules_home: Path, held: list[str]) -> list[Assist]:
    """The assists a routine actually gets: those declared by the rules it HOLDS.

    An assist is part of the rule, not a capability of its own. The user already decided this
    routine practises this rule — `effect.when` is exactly that decision — and an assist only
    changes WHEN its line is read, never what the routine may do. Nothing here can reach a
    routine that does not hold the rule.
    """
    slugs = set(held or [])
    return [a for a in read_library_assists(rules_home) if a.rule in slugs]


# --- the tally -----------------------------------------------------------------------------

def state_path(routine_dir: Path) -> Path:
    return Path(routine_dir) / "state" / STATE_FILE


def record_fire(routine_dir: Path, assist: Assist) -> int:
    """Count one fire, and return the new count.

    Deliberately just a counter, and deliberately engine-written: at the `remind` rung an
    assist costs no turn, so there is no confusion matrix to fill in yet and no reason to
    spend a model's attention labelling one. What this DOES answer is the question precision
    is reviewed by — which assists fire, and how often — so a trigger that fires constantly is
    visible before anyone promotes it to a rung that costs turns. Best-effort: a failed tally
    must never fail the turn that produced it.
    """
    from .paths import atomic_write_json, read_json

    path = state_path(routine_dir)
    raw = read_json(path, {})
    counts = raw if isinstance(raw, dict) else {}
    n = int(counts.get(assist.key) or 0) + 1
    counts[assist.key] = n
    try:
        atomic_write_json(path, dict(sorted(counts.items())))
    except (OSError, ValueError):
        pass
    return n
