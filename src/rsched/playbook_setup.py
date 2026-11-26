"""What a playbook's `setup:` block PROPOSES for the conversation it seeds — sanitized, and
credential-free by construction (D313).

A playbook's MAIN.md front matter may declare the settings its procedure presumes: the
permission docs it is about, the general rules it practises, a working directory, folder access,
and which OAuth providers it needs bound. Until D313 the creation path read only the
instruction body, so a playbook naming `shell` seeded a conversation that could not run a shell
command and said nothing about it.

THE SHAPE OF THE ANSWER THE OPERATOR CHOSE (option b — "pre-fill and show them at
creation; one click to confirm"), over applying them silently (a) and over pre-filling only the
harmless ones (c). So this module's whole job is to compose a PROPOSAL — a thing to be READ —
and it has no apply path at all. Choosing a playbook calls `proposal`, and `proposal` touches no
config: the console renders it, the person confirms it, and the confirmed values then travel the
ORDINARY create form, through the same validators and the same permission cascade every other
conversation goes through (`web/api_conversation_create.py`). That is why option (c) could be
declined safely: a shell-bearing playbook's setup IS shown, and what protects it is the visible
confirmation rather than withholding the field.

THREE BOUNDARIES, each a property of what this function RETURNS rather than a rule someone must
remember:

1. NO CREDENTIAL MATERIAL, ever. `connections` comes back as a sorted list of PROVIDER NAMES —
   never the `{provider: account}` map the create form takes, because an account label is the
   identity a reply would act as. So a confirmed proposal cannot carry one: the console has
   nothing to put in that field, and a person who wants an account bound picks it in the
   composer's own connections card, from the accounts actually connected.

2. NOTHING OUTSIDE THE ALLOWLIST SURVIVES. A playbook is a library document, editable by hand
   and distilled by a model, so `setup:` is untrusted input. Only `FIELDS` is read; every other
   key is dropped and NAMED in `unknown`. A `capabilities:` or `secrets:` key in a playbook
   therefore reaches nothing — a capability is switched on by holding the permission doc that
   requires it, which is the one door `resolve_permission_layers` opens.

3. ONLY WHAT THE LIBRARY ACTUALLY HOLDS. A slug naming no document is dropped (`unknown`), not
   proposed: the create path 400s on an unknown rule, and a pre-fill that cannot be submitted is
   worse than an empty one.

Unresolved `{{placeholders}}` (the `{{workdir}}` convention — see
library-seed/playbooks/machine-shell-task) are dropped and named in `unresolved`: they are
parameters for the person to fill, and a literal "{{workdir}}" offered as a path is a grant
nobody meant to a directory that does not exist.
"""

from __future__ import annotations

import re

#: The `setup:` keys this module reads. Everything else a playbook declares is dropped and
#: named — see boundary 2 above. `connections` is read as a set of provider names only.
FIELDS = ("permissions", "rules", "workdir", "fs_write_roots", "fs_read_roots",
          "connections", "note")

#: The keys whose values are lists of library document slugs, and the attribute of ServerConfig
#: naming the library home each is validated against.
_DOC_FIELDS = {"permissions": "permissions_home", "rules": "rules_home"}

_PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}")


def _texts(value) -> list[str]:
    """A YAML value as a list of non-empty strings — a bare string counts as one entry.

    A hand-edited playbook writes `permissions: shell` as often as it writes a list, and a
    string iterated per character is how the distiller once tagged a playbook `e v a l u a`
    (see workflows/lint.lint_playbook_text). So a string is ONE entry here, never six.
    """
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = [v for v in value if isinstance(v, str)]
    else:
        return []
    return [v.strip() for v in items if v.strip()]


def _providers(value) -> list[str]:
    """The provider NAMES a `connections:` declaration asks for — never an account label.

    The seeds write `connections: {}` or `connections: {google: ''}`: a map whose keys are
    providers and whose values would be accounts. Only the keys come back, so boundary 1 holds
    structurally: what this returns cannot be submitted as a connection binding.
    """
    if isinstance(value, dict):
        return sorted(k.strip() for k in value if isinstance(k, str) and k.strip())
    return sorted(set(_texts(value)))


def proposal(meta: dict, *, rules: list[str] | set[str] | None = None,
             permissions: list[str] | set[str] | None = None) -> dict:
    """The credential-free setup a playbook PROPOSES, ready to render; applies nothing.

    `rules` and `permissions` are the slugs the live library holds (
    `readmodels.library_reads.doc_slugs`); a slug outside them is dropped into `unknown` rather
    than proposed, because the create path refuses one and a pre-fill that cannot be submitted
    helps nobody.

    Keys: the seven of `FIELDS` (`connections` as provider names), plus `unresolved` (the
    `{{placeholders}}` dropped, for the person to fill in) and `unknown` (slugs the library
    does not hold and keys outside `FIELDS`, so a playbook declaring something unreachable says
    so on the page instead of failing silently). `declares` is True when there is anything at
    all to confirm.
    """
    declared = meta.get("setup")
    raw: dict = declared if isinstance(declared, dict) else {}
    known = {"rules": set(rules or ()), "permissions": set(permissions or ())}
    out: dict = {"unresolved": [], "unknown": []}
    out["unknown"] += sorted(f"setup.{k}" for k in raw if k not in FIELDS)
    for field in _DOC_FIELDS:
        slugs = _texts(raw.get(field))
        out[field] = [s for s in slugs if s in known[field]]
        out["unknown"] += [f"{field}:{s}" for s in slugs if s not in known[field]]
    for field in ("fs_write_roots", "fs_read_roots"):
        paths = _texts(raw.get(field))
        out[field] = [p for p in paths if not _PLACEHOLDER.search(p)]
        out["unresolved"] += [p for p in paths if _PLACEHOLDER.search(p)]
    workdir = " ".join(_texts(raw.get("workdir"))[:1])
    if _PLACEHOLDER.search(workdir):
        out["unresolved"].append(workdir)
        workdir = ""
    out["workdir"] = workdir
    out["connections"] = _providers(raw.get("connections"))
    out["note"] = " ".join(str(raw.get("note") or "").split())
    out["unresolved"] = sorted(set(out["unresolved"]))
    out["declares"] = any(out[f] for f in FIELDS) or bool(out["unresolved"])
    return out


def from_config(cfg) -> dict:
    """The `setup:` block a SAVED playbook keeps — the settings the conversation actually ran
    with, minus anything that identifies an account (F954).

    Captured when a conversation is distilled into a playbook, so the next conversation seeded
    from it is offered what this one needed rather than nothing. `connections` keeps the
    PROVIDER names and discards the accounts for boundary 1's reason: the playbook is a library
    document, read by anyone who can read the library, and an account label in it is a standing
    record of whose mailbox the work was done in.
    """
    conns = getattr(cfg, "connections", None) or {}
    setup = {
        "permissions": sorted(getattr(cfg, "permissions", None) or []),
        "rules": sorted(getattr(cfg, "rules", None) or []),
        "workdir": str(getattr(cfg, "workdir", "") or ""),
        "fs_write_roots": sorted(getattr(cfg, "fs_write_roots", None) or []),
        "fs_read_roots": sorted(getattr(cfg, "fs_read_roots", None) or []),
        "connections": dict.fromkeys(sorted(conns), "") if isinstance(conns, dict) else {},
    }
    return {k: v for k, v in setup.items() if v}
