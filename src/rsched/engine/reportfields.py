"""The `report` action's DISPOSAL grammar — `closes`, `answers`, `settles`, `supersedes`.

Moved out of `engine/actions.py` for the reason `engine/jobfields.py` and
`engine/sidefields.py` were: that file is the action contract's single home, it is over the
repo's size bar and may only shrink, so a kind whose grammar is a block of its own goes to its
own module rather than growing it. Nothing about the rules changed in the move; the messages
are byte-identical, because a refusal a run has learned to read is a surface too.

What the four fields mean to each other is the whole content of this file. `answers` records
the ONE exchange a reply belongs to, `settles` the other rows it declares finished,
`supersedes` the rows it TAKES OVER (they stay live under this report), and `closes` says the
reply itself is born settled. The contradictions refused here are the pairs that say opposite
things about who holds the work next — each one was a real shape: a row settled in prose and
open in the ledger (F497), a fold that silently won over an answer.
"""

from __future__ import annotations

from ..reports import REPORT_ID_RE


def field_problems(obj: dict) -> list[str]:
    """Every problem with this report's disposal fields — one sentence each, in the voice the
    run already reads refusals in.
    """
    problems: list[str] = []
    # `closes` is a property OF a disposal — a terminal acknowledgment. `answers` disposes of
    # ONE exchange, `settles` of several; with neither there is nothing to complete, so a bare
    # closes is a contradiction, not a no-op. Before D134 only `answers` counted, which is why
    # a report that asked nothing back could not be born settled at all: it answers no single
    # row, so `closes` was refused and the carrier stayed open forever (F497).
    if (obj.get("closes")
            and not str(obj.get("answers") or "").strip()
            and not obj.get("settles")):
        problems.append("kind=report: 'closes' is valid only together with 'answers' or "
                        "'settles' — it marks the disposal as completing those exchanges")
    # `answers` is ONE report id, held to the grammar `settles` and `supersedes` are. The ledger
    # matches it against ids, so free text there disposed of nothing — while the run, and its
    # observation, believed the exchange answered (the settled-in-prose, open-in-the-ledger
    # shape F497 measured).
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
            # Folding a row makes this report its thread, so it stays live until this one
            # settles; settling it declares it finished now. Both at once on the same id is a
            # contradiction the read model would have to break one way or the other.
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
            # Answering a thread ends it; absorbing one continues it here. Both at once on the
            # same id is a contradiction, and the fold would win silently — leaving the row
            # reading THIS report's status while the run believed it had closed it.
            problems.append(f"kind=report: {answered} cannot be both 'answers' and "
                            "'supersedes' — answering ENDS that thread, taking it over "
                            "CONTINUES it here. Pick one")
    return problems
