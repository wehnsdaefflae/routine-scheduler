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
the reasoning that produced them, and reasoning is never in the trace. `fix-the-cause` is the
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

#: The payload axis is `remind -> action -> hold`. The design note spelled the middle rung
#: `scaffold -> do`, and said neither existed "because each needs a helper channel that is not
#: built". That channel IS built now — `hooks/perform.py` runs one authored action at a hook
#: point through the model's own path, with no model turn — so the rung exists, and it is
#: named `action` rather than `do` for ONE reason: `hooks/points.PAYLOADS` already calls it
#: that, and a second name for one thing is how two tables come to disagree. The correction is
#: the design note's, not this module's (`tests/test_hooks_points.py` pins the two together).
#:
#: `scaffold` — pre-filling a draft for the model to work from — is still unbuilt, and stays
#: out of the vocabulary: an enum value the engine cannot honour would be a lie in the
#: library's own schema.
PAYLOADS = ("remind", "action", "hold")

#: moment -> the payloads it can carry. `pre-action` can ONLY hold (there is no way to reach a
#: chosen action without stopping it), and the free moments can only remind or ACT (a hold
#: needs an action to stop, and neither of them has one).
#:
#: `action` is barred from the two moments that CHANGE CONTROL FLOW, and that is the operator's
#: ruling rather than a cautious first cut: *"No veto ever — a hook performs and reports; only a
#: hold (prose to the model) can stop an action"* (2026-10-26). A performed action never changes
#: control flow, so it may not live where control flow is decided (`pre-action` prevents an
#: action, `pre-finish` defers a finish) — the same line `hooks/points.ACTION_POINTS` derives.
#:
#: Of the two FREE moments it carries `boundary` only, and the reason is ORDERING, not policy.
#: The observation-moment assists are asked BEFORE their observation is recorded, so the record
#: can name what fired on it (`engine/assist.recorded`, which a resumed leg rebuilds its guards
#: from) — performing there would file the hook's own observation AHEAD of the one that
#: triggered it, and a transcript that lies about order is worse than a rung that waits. The
#: turn boundary is where an authored action already runs with no model turn
#: (`engine/control.run_user_command`, the path `hooks/perform.py` was lifted from), so it is
#: the seam, not a compromise. `post-action` keeps `action` in the point registry because the
#: reminder layer — whose author IS the run, and whose fire site is after the record — is the
#: mechanism that will fire there.
MOMENT_PAYLOADS = {"pre-action": ("hold",), "observation": ("remind",),
                   "boundary": ("remind", "action"), "pre-finish": ("remind",)}

#: The action kinds an `action` assist may NOT perform: the ones that STEER a run rather than
#: having an effect in the world. `engine/commands.py` draws this exact line for the user's own
#: slash commands — *"Loop-control kinds (spawn/subtask/wait/ask_user/finish/…) are deliberately
#: NOT commands: they steer the model's run; ask the assistant instead"* — and it is sharper
#: here, for a reason that is about authorship rather than taste: an assist is declared ONCE in
#: a library rule and fires in every routine that holds it, with no model turn. A `finish` or a
#: `wait` performed there would change where the run goes without the model ever learning of it,
#: which is the thing the operator ruled out: *"No veto ever — a hook performs and reports; only
#: a hold (prose to the model) can stop an action"*.
#:
#: This bounds the ASSIST surface, not `hooks/perform.py` — his *"Any action. This includes utils
#: and therefore scripts"* stands for the seam, and a mechanism whose author IS the run (a
#: routine's own reminder) is not bound by the reason above. A kind leaving this set is a
#: decision, which is why the set is named here rather than spelled into a condition.
STEERING_KINDS = ("finish", "spawn", "subtask", "detach", "wait", "kill", "ask_user",
                  "goal", "do_nothing", "create_routine", "manage_lane")

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
    #: The action an `action`-payload assist PERFORMS when it fires, as the model's own action
    #: dict (`hooks/perform.py` runs it through the model's path, so it is validated against the
    #: firing routine's capabilities at fire time and can never exceed them). Empty for every
    #: other payload. A frozen mapping is not expressible in a dataclass default, so this is the
    #: one field a reader must not mutate — `perform` takes a copy.
    action: dict | None = None

    @property
    def key(self) -> str:
        """The stable identity of one assist — the key of its once-per-run fire guard (named in
        the transcript, so a resumed leg rebuilds the guard) and of its tally.
        """
        return f"{self.rule}/{self.id}"


def _read_action(item: dict, payload: str, where: str) -> tuple[dict | None, str]:
    """The `action` an assist performs, or the problem with it — `(action, "")` / `(None, why)`.

    Separate from `normalize_assists` so that function keeps one branch per field: this is four
    checks about one field, and the authoring error has to say which.

    What is NOT checked here: whether the action is valid, and whether the routine may take it.
    Both are answered at FIRE time by the model's own gates (`hooks/perform.py` →
    `validate(ACTION_SCHEMA)` → `validate_action(allowed_kinds=…, grants=…)`), and they must be:
    a library rule is authored once and held by routines with different capabilities, so "may
    this run do this" has no answer at authoring time. What IS checked is the part that is the
    same everywhere — that there is an action, that it names a kind, and that the kind is not
    one that steers a run.
    """
    action = item.get("action")
    if payload != "action":
        if action is not None:
            return None, (f"{where}: 'action' belongs to an `action` payload — a {payload!r} "
                          "assist surfaces its line and performs nothing")
        return None, ""
    if not isinstance(action, dict) or not action:
        return None, (f"{where}: an `action` payload needs an 'action' mapping — the action "
                      "dict to perform, in the same form the model emits")
    kind = str(action.get("kind") or "")
    if not kind:
        return None, f"{where}: the 'action' names no 'kind'"
    if kind in STEERING_KINDS:
        return None, (f"{where}: {kind!r} STEERS the run, so an assist may not perform it — a "
                      f"hook performs and reports, and only a hold can change what a run does. "
                      f"The kinds no assist may perform: {list(STEERING_KINDS)}")
    return dict(action), ""


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
        unknown = set(item) - {"id", "moment", "predicate", "payload", "line", "action"}
        if unknown:
            problems.append(f"{where}: unknown key(s) {sorted(unknown)} — an assist is "
                            "id / moment / predicate / payload / line, plus `action` for an "
                            "`action` payload")
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
                            "`scaffold` rung (pre-filling a draft) is not built, so it is "
                            "deliberately not in the vocabulary")
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
        action, why = _read_action(item, payload, where)
        if why:
            problems.append(why)
            continue
        out.append(Assist(rule=rule, id=aid, moment=moment, predicate=predicate,
                          payload=payload, line=line, action=action))
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
    try:
        n = int(counts.get(assist.key) or 0) + 1
    except (TypeError, ValueError):
        n = 1        # a count a hand edit broke starts over rather than failing the turn
    counts[assist.key] = n
    try:
        atomic_write_json(path, dict(sorted(counts.items())))
    except (OSError, ValueError):
        pass
    return n
