"""A run's BEHAVIOUR KEY — what made it, read from its fingerprint (engine/runrecord.py) — and the
SEGMENTS a routine's runs fall into: maximal stretches of consecutive runs whose keys agree.
A CHANGE is the boundary between two segments (readmodels/change_effects.py).

The key's components: the recipe, the behaviour-relevant config, the main model by catalog name
and by provider id, its effort, the deliberation level, a model trial — and the held rules, each
by the hash of its text. The engine release is NOT one: releases land several a day and are read
across the fleet instead (readmodels/change_fleet.py).

A component's value is a string (`""` is a real value — no trial, no effort set) or None, which is
UNKNOWN. A run rebuilt from history (`fingerprint.source == "reconstructed"`,
migrate_runrecords.py) names what it could not recover in `fingerprint.unknown`; an unknown
component matches any value and is never a change, because a gap in the evidence is not a change
in the routine.
"""

from __future__ import annotations

#: The scalar components of a behaviour key; the held rules are the rest (`rule:<slug>`).
SCALARS = ("recipe", "config", "model", "model_id", "effort", "deliberation", "trial")

Key = dict[str, str | None]
FullKey = tuple[Key, dict[str, str] | None]


def components(rec: dict) -> FullKey:
    """One run's behaviour key: the scalar components, and the held rules (`{slug: text hash}`)
    — None when the run's rules are unknown, so a rule missing from a KNOWN set reads as not
    held while a rule missing from an unknown one reads as nothing at all.
    """
    fp = rec.get("fingerprint") or {}
    unknown = set(fp.get("unknown") or ())
    scalars: Key = {k: None if k in unknown else str(fp.get(k) or "") for k in SCALARS}
    if "recipe" not in unknown:
        scalars["recipe"] = str(rec.get("recipe_commit") or fp.get("recipe") or "")
    rules = None if "rules" in unknown else {str(k): str(v)
                                             for k, v in (fp.get("rules") or {}).items()}
    return scalars, rules


def agree(a: FullKey, b: FullKey) -> bool:
    """No component known on BOTH sides differs."""
    (sa, ra), (sb, rb) = a, b
    if any(sa[k] is not None and sb[k] is not None and sa[k] != sb[k] for k in SCALARS):
        return False
    return ra is None or rb is None or ra == rb


def _merge(rep: FullKey, key: FullKey) -> FullKey:
    """A segment's representative key: every component any of its runs knew."""
    (sa, ra), (sb, rb) = rep, key
    return {k: sa[k] if sa[k] is not None else sb[k] for k in SCALARS}, (
        ra if ra is not None else rb)


def diff(old: FullKey, new: FullKey) -> list[dict]:
    """What changed between two keys — only components known on both sides."""
    (so, ro), (sn, rn) = old, new
    out = [{"kind": k, "name": "", "from": so[k], "to": sn[k]} for k in SCALARS
           if so[k] is not None and sn[k] is not None and so[k] != sn[k]]
    if ro is not None and rn is not None:
        out += [{"kind": "rule", "name": slug, "from": ro.get(slug, ""), "to": rn.get(slug, "")}
                for slug in sorted({*ro, *rn}) if ro.get(slug, "") != rn.get(slug, "")]
    return out


def segments(runs: list[dict]) -> list[tuple[FullKey, list[dict]]]:
    """Consecutive runs whose keys agree, each with its representative key."""
    segs: list[tuple[FullKey, list[dict]]] = []
    for rec in runs:
        key = components(rec)
        if segs and agree(segs[-1][0], key):
            segs[-1] = (_merge(segs[-1][0], key), [*segs[-1][1], rec])
        else:
            segs.append((key, [rec]))
    return segs


def reconstructed(runs: list[dict]) -> bool:
    return any((r.get("fingerprint") or {}).get("source") == "reconstructed" for r in runs)


def engine(rec: dict) -> str:
    return str((rec.get("fingerprint") or {}).get("engine") or "")


def version_key(v: str) -> tuple:
    """An engine release as a sortable tuple (`0.398.0` after `0.39.10`)."""
    return tuple(int(p) if p.isdigit() else 0 for p in v.split("."))


def engines(runs: list[dict]) -> list[str]:
    """The releases a run window spans, oldest first."""
    return sorted({engine(r) for r in runs} - {""}, key=version_key)
