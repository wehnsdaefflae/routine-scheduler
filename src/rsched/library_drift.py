"""Which library gap is THE SAME QUESTION — the one rule, so an answered one stays answered.

F604: a drift card is a NOTICE with nothing to materialize, so the operator's only answer to
one is `dismiss` — and dismissing ran `pending.drop`, which unlinks the record. The answer
therefore left no trace anywhere. `daemon/library_watch._report` skips a gap that already has an
OPEN record (`already`), and an answered one is not open any more, so the next library commit —
any commit, about anything — re-queued the identical question and named itself as the cause.
The operator got asked again about something he had settled, with misleading provenance
(operator message 2026-10-01, library-export handover, item 7; R2125).

The missing piece was never the record. It is the gap's IDENTITY. A drift decision is about a
gap — this routine, this thing it holds that the library no longer satisfies, for this stated
reason — and that identity does not move when an unrelated util gains a secret. So a settled
answer is written against the FINGERPRINT of the gap, and the watcher asks again only when the
fingerprint differs: a different node, or the same node gone wrong for a different reason, is a
question the operator has not answered.

What is IN the fingerprint is the whole design:

- the routine, because the same missing secret on two routines is two decisions (two different
  people's work is blocked, and the remedies differ);
- the node id — the entity the gap is about (`secret:ZULIP_API_KEY`, `util:zulip`);
- the node STATE, because `absent` and `denied` are not the same gap: "add it to the store" and
  "you declined it forever" are different questions with different answers;
- the node's `why`, the stated reason, because "needed by zulip" becoming "needed by zulip,
  signal" is a wider claim on the same entity — and a reason the operator never read is a
  question he never answered.

What is NOT in it: the library HEAD, the commit subject, the time. Those are what made the
defect — they change on every commit, so keying on them means re-asking on every commit.

A settlement lives exactly as long as its gap, the same invariant `library_watch` already keeps
for the records themselves: a gap that CLOSES (the secret arrives, the rule is unbound, the
routine is archived) forgets the answer, so the same gap opening again later is a new question
rather than a silence nobody chose. That is `library_watch._withdraw_closed`'s second half.
"""

from __future__ import annotations

from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, read_json

#: Where the operator's settled drift answers live: engine-owned derived state beside
#: `library-head.json` (`daemon/library_watch._MARKER`), explicitly never config. A missing
#: file means "nothing settled yet", which is the correct reading for a fresh install.
STORE = Path(".control") / "library-drift-settled.json"


def fingerprint(routine: str, node: dict) -> str:
    """The identity of one gap — what makes two drift reports THE SAME QUESTION.

    A readable composite rather than a digest: this ends up in a record on disk and in a store
    an operator may well open, and a reader who can see WHY two gaps are the same (or are not)
    can check this rule without running it.
    """
    return "|".join((routine, str(node.get("id") or ""), str(node.get("state") or ""),
                     str(node.get("why") or "")))


def of_record(rec: dict) -> str:
    """The fingerprint of a drift record on disk.

    New records carry it in `fields["gap"]`, written by the watcher that applied the rule. A
    record queued before this module existed carries the whole resolved node instead, which is
    everything the rule needs — so it is derived rather than refused: records already on disk
    must still read, and the operator's dismissal of one has to settle the gap it is about.
    """
    fields = rec.get("fields") or {}
    if stored := str(fields.get("gap") or ""):
        return stored
    node = fields.get("node")
    return fingerprint(str(rec.get("routine") or ""), node if isinstance(node, dict) else {})


def describe(node: dict) -> str:
    """The library things this gap is about, in words — "the zulip util", "the status-page doc".

    Read off the row's machine-readable `source` (`readmodels/surface_nodes._node`), never
    parsed out of its prose: joining on prose is what invariant 5 forbids. Empty when the row
    names no library thing at all, which is a real case the wording has to handle rather than
    paper over.
    """
    src = node.get("source") or {}
    names = [f"the {name} util" for name in (src.get("utils") or [])]
    if doc := str(src.get("doc") or ""):
        names.append(f"the {doc} doc")
    return " and ".join(names)


def source_paths(node: dict) -> list[str]:
    """Where in the library this gap's own things live — the pathspec that answers "did this
    change touch what the gap is about?".

    A doc slug does not say whether it is a permission or a rule, so both are named; a pathspec
    matching nothing simply matches nothing.
    """
    src = node.get("source") or {}
    paths = [f"utils/{name}" for name in (src.get("utils") or [])]
    if doc := str(src.get("doc") or ""):
        paths += [f"permissions/{doc}.md", f"rules/{doc}.md"]
    return paths


def trigger(head: str, node: dict, commits: list[tuple[str, str]]) -> str:
    """What actually OPENED this gap, in the one line the operator reads on the card.

    The second half of F604. The old wording was `After library change <head8> (<subject>)`,
    where `head` is whatever commit happened to arrive when the gap was noticed — so a routine
    broken by a util edit was reported "after" an unrelated docs commit, and the operator read
    that as the cause. It was never a claim the watcher could support: it compared HEADs, and
    the commit it named is simply the newer of the two.

    So the claim is narrowed to what the history actually shows. `commits` is the commits in the
    change that touched THIS gap's own files (`source_paths`), newest first:

    - exactly one — that commit is the trigger, and the line says so;
    - several — one of them opened it; the count and the latest are what is known, and the line
      does not pick a culprit it cannot identify;
    - none — the library moved without touching anything this gap is about (the gap opened on
      the routine's own side, or the history does not reach back to the last-seen HEAD). The
      line then states the library STATE the gap was found at and says plainly that what opened
      it is not known, which is the honest reading of exactly the same facts.
    """
    what = describe(node)
    if not commits:
        if not what:
            return (f"Found at library {head[:8]}; this gap names no library file, so what "
                    "opened it is not known from the library's history")
        return (f"Found at library {head[:8]}; nothing in that change touched {what}, so the "
                "commit that opened this gap is not known")
    sha, subject = commits[0]
    if len(commits) == 1:
        return f"Opened by {sha[:8]} ({subject}), which changed {what}"
    return (f"Opened by one of {len(commits)} commits that changed {what}, latest "
            f"{sha[:8]} ({subject})")


def _path(routines_home: Path) -> Path:
    return routines_home / STORE


def load(routines_home: Path) -> dict[str, dict]:
    """Every settled gap, by fingerprint. Unreadable or absent is empty — a store this watcher
    cannot read must never be the reason a scheduler tick fails.
    """
    raw = read_json(_path(routines_home))
    settled = raw.get("settled") if isinstance(raw, dict) else None
    if not isinstance(settled, dict):
        return {}
    return {str(k): v for k, v in settled.items() if isinstance(v, dict)}


def settle(routines_home: Path, rec: dict, reason: str = "") -> str:
    """Write down that the operator answered THIS gap, and return the fingerprint.

    Called from the dismissal (`web/api_pending.discard`), which is the only answer a drift card
    takes. `routine` and `entity` are kept beside the answer so a closed gap can retire it
    without re-deriving anything, and `head` so the store says which notice he was reading.
    """
    key = of_record(rec)
    fields = rec.get("fields") or {}
    settled = load(routines_home)
    settled[key] = {"routine": str(rec.get("routine") or ""),
                    "entity": str(fields.get("entity") or ""),
                    "head": str(fields.get("head") or ""),
                    "reason": reason, "at": now_iso()}
    atomic_write_json(_path(routines_home), {"settled": settled})
    return key


def forget(routines_home: Path, keys: list[str]) -> list[str]:
    """Drop these settled answers — the gaps they answered are closed, so the next time one
    opens it is a new question. Returns the keys actually removed.
    """
    settled = load(routines_home)
    gone = [k for k in keys if settled.pop(k, None) is not None]
    if gone:
        atomic_write_json(_path(routines_home), {"settled": settled})
    return gone
