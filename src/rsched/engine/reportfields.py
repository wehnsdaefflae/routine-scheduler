"""The `report` action's DISPOSAL grammar — `closes`, `answers`, `settles`, `supersedes`.

`answers` records the ONE exchange a reply belongs to, `settles` the other rows it declares
finished, `supersedes` the rows it TAKES OVER (they stay live under this report), and `closes`
says the reply itself is born settled. The contradictions refused here are the pairs that say
opposite things about who holds the work next. The refusal wording is a surface runs read.
"""

from __future__ import annotations

from ..reports import REPORT_ID_RE


def field_problems(obj: dict) -> list[str]:
    """Every problem with this report's disposal fields — one sentence each, in the voice the
    run already reads refusals in.
    """
    problems: list[str] = []
    # EVERY rule below is about a LEDGER ROW, and a run-tree message (`parent`,
    # `child:<label>`) has none (F859). It is not refused: `runtree.send` delivers the hook and
    # NAMES the disposal fields it ignored, so the run is not left believing a row disposed of.
    from .runtree import is_runtree  # local: runtree imports inbox, this does not
    if is_runtree(obj.get("target") or ""):
        return []
    # `closes` is a property OF a disposal (`answers` one exchange, `settles` several); with
    # neither there is nothing to complete. The refusal tells an ORIGINATING notice what to do
    # (drop `closes`): a closure is kept out of the run gate's inbox predicate
    # (`daemon/gate_builtin`), so marking one would hide the notice rather than deliver it
    # quietly.
    if (obj.get("closes")
            and not str(obj.get("answers") or "").strip()
            and not obj.get("settles")):
        problems.append(
            "kind=report: 'closes' is valid only together with 'answers' (the ONE exchange "
            "this reply belongs to) or 'settles' (every row it finishes) — it marks a disposal "
            "as complete, and a report that originates something completes none. Filing a "
            "notice that asks nothing back: leave 'closes' out. The row is then the recipient's "
            "to dispose of, which is what puts it in front of them — a closure does not admit "
            "a gated fire on purpose, so it is not the quiet way to be heard.")
    # `answers` is ONE report id: the ledger matches ids, so free text would dispose of
    # nothing while the run believed the exchange answered.
    if ((answered := str(obj.get("answers") or "").strip())
            and not REPORT_ID_RE.match(answered.upper())):
        problems.append(f"kind=report: 'answers' takes the ONE report id you received and are "
                        f"replying to, like R123 — not {answered[:60]!r}")
    # Settling rows CLAIMS they are finished. It needs no `target` (each settled row already has
    # its own raiser) but it does need well-formed ids, and a row cannot be both answered and
    # settled by one reply, nor both settled and folded — those say opposite things about who
    # holds the work next.
    if obj.get("settles"):
        if bad := [str(i) for i in obj["settles"]
                   if not REPORT_ID_RE.match(str(i).strip().upper())]:
            problems.append(f"kind=report: 'settles' takes report ids like R123 — not "
                            f"{', '.join(bad[:3])}")
        elif (answered := str(obj.get("answers") or "").strip().upper()) and answered in [
                str(i).strip().upper() for i in obj["settles"]]:
            problems.append(f"kind=report: {answered} is already settled by 'answers' — naming "
                            "it in 'settles' too says the same thing twice. List only the "
                            "OTHER rows this reply disposes of")
        elif overlap := sorted({str(i).strip().upper() for i in obj["settles"]}
                               & {str(i).strip().upper() for i in (obj.get("supersedes") or [])}):
            # Folding keeps a row live under this report; settling declares it finished now.
            problems.append(f"kind=report: {', '.join(overlap[:3])} cannot be both 'settles' "
                            "and 'supersedes' — settling DECLARES a row finished, taking it "
                            "over CONTINUES it here. Pick one")
    # Taking a row over means becoming its OWNER's thread. Without a target there is no owner
    # to become, and the folded rows would leave triage for nowhere.
    if obj.get("supersedes"):
        if not str(obj.get("target") or "").strip():
            problems.append("kind=report: 'supersedes' needs 'target' — folding rows into this "
                            "report hands them to that owner, so name who is taking them")
        elif bad := [str(i) for i in obj["supersedes"]
                     if not REPORT_ID_RE.match(str(i).strip().upper())]:
            problems.append(f"kind=report: 'supersedes' takes report ids like R123 — not "
                            f"{', '.join(bad[:3])}")
        elif (answered := str(obj.get("answers") or "").strip().upper()) and answered in [
                str(i).strip().upper() for i in obj["supersedes"]]:
            # Answering ends a thread; absorbing continues it — and the fold would win silently.
            problems.append(f"kind=report: {answered} cannot be both 'answers' and "
                            "'supersedes' — answering ENDS that thread, taking it over "
                            "CONTINUES it here. Pick one")
    return problems
