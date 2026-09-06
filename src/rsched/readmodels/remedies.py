"""The setup surface said in WORDS — one `fix` turned into the sentence that settles it.

`surface.py` answers "what does this routine still need?" and stops one step short on purpose:
a node's `fix` names WHAT has to happen (`{"kind": "add_secret", "name": "FOO_TOKEN"}`) and
never where a reader goes to do it, because that half depends entirely on who is reading. The
console maps one kind to one panel and renders a link. The two callers with no panels — `rsched
validate` on a terminal, the engine's boot note in front of a run — need the same map in prose,
which is this module: the kind vocabulary plus the flat lines a surface renders to.

It is not part of the read model because it reads nothing. No config is joined against the
library here and no verdict is decided; a wording change in this file changes what an operator
is TOLD and never what is true. Keeping the join and the phrasing apart is what lets each be
edited without re-reading the other. It is also why the vocabulary is a table rather than a
branch per caller: adding a node type is adding its words, in one place, or the CLI renders a
gap with no remedy while the console offers one.

Terseness is a hard constraint, not a style. `surface_lines` is what the boot note carries, so
every line here is PROMPT TEXT a run pays for on the turn it boots and on every turn after that
survives compaction. A remedy is therefore one clause naming the action and its subject — "add
FOO_TOKEN to the secrets store" — with no narration of why it matters: the row's own `why` and
`effect` already said that, so a diagnostic that costs tokens earns every one of them.
"""

from __future__ import annotations

from collections import defaultdict

from .surface import BLOCKS, INTERRUPTS, NOTE, OK

#: Every `fix` kind and how it READS with no screen to click. This is the whole vocabulary in
#: one place: a kind absent from here renders no remedy, so adding a node type and adding its
#: words is one edit.
#:
#: A `kind:variant` entry is a SECOND wording of one kind, for a fix whose payload changes what
#: can honestly be said. `:any` is the need "one of this class" (`"*"`), which no sentence can
#: name. An OWNER variant is the same need read by somebody who cannot act on it where they are
#: standing: a capability the DOMAIN hands down survives the routine's own save, so telling
#: that reader to drop it here is the console's broken link written out in prose. A `:doc`
#: variant is the case where the obvious act is not merely elsewhere but SELF-UNDOING — a save
#: raises the mapping to cover every held doc, so a util one of them requires comes straight
#: back; the only remedy left is to stop holding the doc.
REMEDIES: dict[str, str] = {
    "switch_on": "switch on {missing} in this routine's capabilities",
    "cover_or_drop": "hold a conduct doc that requires it, or drop it from this routine's "
                     "capabilities",
    "cover_or_drop:domain": "hold a conduct doc that requires it, or drop it from the "
                            "{domain} domain that supplies it",
    "grant": "record an exposure decision in this routine's grants",
    "clear_grant": "clear the refusal recorded in this routine's grants",
    "add_secret": "add {name} to the secrets store",
    "add_root": "grant a {mode} root covering {path}",
    "add_root:any": "grant this routine a {mode} root",
    "bind_machine": "bind the machine {name} to this routine",
    "bind_machine:any": "bind a machine to this routine",
    "bind_connection": "bind an account for {provider}",
    "bind_connection:any": "bind an account for the connection it presumes",
    "install_util": "drop {name} from this routine's capabilities; only a run writes a util",
    "install_util:domain": "drop {name} from the {domain} domain that supplies it; only a run "
                           "writes a util",
    "install_util:doc": "stop holding {doc}, which requires it and puts any drop straight back; "
                        "only a run writes a util",
    "set_schedule": "give it a cron of its own, or put it in a scheduled lane",
    "lane_schedule": "clear this routine's cron so the file says what happens, or reschedule "
                     "the lane {name}",
    "fix_phase": "have the recipe record the phase under `{expected}`",
}

#: Which param decides between a kind's named and `:any` wording.
_ANY_PARAM = {"add_root": "path", "bind_machine": "name", "bind_connection": "provider"}

#: Every `fix` param a template may name, which is the second half of the vocabulary the table
#: above is the first half of. It is what `_remedy` fills the placeholders FROM, so a template
#: naming anything else renders an empty gap where a subject belongs —
#: `tests/test_surface.py` scans the `{…}` of every template against this tuple, in both
#: directions, so neither the words nor the params can grow past the other unnoticed.
#:
#: A list param joins with commas (`missing`); everything else is said as it stands.
_PARAMS = ("missing", "name", "mode", "path", "domain", "provider", "expected", "doc")


def _params(fix: dict) -> defaultdict[str, str]:
    """One fix's params as the mapping a template formats against — DEFAULTING, never raising.

    Two things are absent for entirely different reasons and both have to yield a thin sentence
    rather than an exception: a half-filled fix (the param exists, this payload has no value
    for it) and a template naming a param nobody put in `_PARAMS`. A remedy is rendered inside
    a diagnostic — `rsched validate` on a terminal, or the boot note in front of a run — where
    neither `engine/boot.py` nor `cli.py` guards against a KeyError raised in here, so a wording
    edit that misspells one placeholder would end the runs it was meant to inform.
    """
    out: defaultdict[str, str] = defaultdict(str)
    for param in _PARAMS:
        value = fix.get(param)
        out[param] = (", ".join(str(v) for v in value) if isinstance(value, list)
                      else str(value or ""))
    return out


def _wording(fix: dict) -> str:
    """Which REMEDIES entry this payload selects — the kind, or one of its variants.

    Variants are asked most specific first. A covering DOC comes before an OWNER because they
    differ in kind rather than in degree: an owner says the same act is performed somewhere
    this reader is not standing, a doc says the obvious act undoes itself and the remedy is a
    different one. A payload carrying neither falls to the kind's base sentence, which is also
    what an `owner` or a `doc` with no wording of its own does.
    """
    kind = str(fix.get("kind") or "")
    vague = _ANY_PARAM.get(kind)
    if vague and str(fix.get(vague) or "*") == "*":
        kind += ":any"
    for variant in ("doc" if fix.get("doc") else "", str(fix.get("owner") or "")):
        if variant and f"{kind}:{variant}" in REMEDIES:
            return f"{kind}:{variant}"
    return kind


def _remedy(fix: dict) -> str:
    """One fix in WORDS. `rsched validate` and the boot note ask the same "where do I fix
    this?" and have no panel to click, so each kind spells its remedy here as well.

    A kind absent from the table renders nothing, which is what a caller with no remedy to
    offer shows. No absent param raises, whether the payload lacks it or the vocabulary never
    had it (`_params`); a malformed template is caught at the gate, by the same test that binds
    the placeholders (`tests/test_surface.py`).
    """
    template = REMEDIES.get(_wording(fix))
    return template.format_map(_params(fix)) if template else ""


def surface_lines(surface: dict, severities: tuple[str, ...] | None = None) -> list[str]:
    """The surface as flat text — what `rsched validate` prints and what the engine files as a
    boot note. One line per unmet node, each ending in the remedy its `fix` names; a ready
    routine yields nothing at all. `severities` narrows it (the engine passes
    `surface.BOOT_SEVERITIES`); the default shows every unmet node.

    A row with no `fix` ends at its effect, which is the whole reading of an absent one: the
    row states something true that nothing has to be done about.
    """
    label = {BLOCKS: "FAIL ", INTERRUPTS: "WARN ", NOTE: "NOTE "}
    out = []
    for n in surface["nodes"]:
        if n["severity"] == OK or (severities and n["severity"] not in severities):
            continue
        tail = f" — {n['effect']}" if n["effect"] else ""
        remedy = _remedy(n.get("fix") or {})
        out.append(f"{label[n['severity']]} {n['id']}: {n['why']}{tail}"
                   + (f" — fix: {remedy}" if remedy else ""))
    return out
