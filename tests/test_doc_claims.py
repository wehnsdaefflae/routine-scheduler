"""docs/ is the system's self-portrait — three of its claim types, made falsifiable corpus-wide.

The Help tab (`static/views/help.js`) serves this repo's own `docs/*.md`, rebuilt at boot from
the source the daemon is running (`docs_build.GUIDE_ORDER`), beside the generated pdoc
reference, so a reader is entitled to read the guides as what the system IS. Two checks existed:
`tests/test_doc_counts.py` pins 3 claims in 2 documents (and the NAMES behind two of them), and
`tests/test_prompt_anatomy_example.py` pins §5 of one. The other ~37 guides were unchecked and
served as the live system — F672 measured ONE section of ONE of them and found three
simultaneous drifts, one of which (a `subtask` bullet offering `workflow: "generate"`, real for
`create_routine` alone) would have had a run's child REFUSED.

This file generalises the count check to every guide and adds the two other claim types that
need no judgement about prose:

1. **A written-out count is a claim about a `len()`.** Each `COUNTS` entry binds a prose pattern
   to the collection it counts. The entries are explicit, not inferred: of 598 number+noun
   phrases in these guides almost every one is a magnitude (minutes, turns, tokens, lines) or a
   dated fleet measurement ("31 of 32 routines"), and neither is a `len()`. Two of the three
   entries were RED when this file was written — "16 rules" against 17 files in
   `library-seed/rules/`, "19 docs" against 18 in `library-seed/permissions/`.
2. **A backticked identifier is a claim that the symbol exists.** An ALL_CAPS_UNDERSCORED,
   snake_cased or dotted token in backticks must still appear in the source. The underscore (or
   the dot) is what separates a symbol claim from an emphasised word, and it is what the F672
   class of error shows up in: `KIND_PROSE`, `BLOCKED_EVENTS`, `roles_problem`.
3. **A cited path must resolve, and a cited `path:line` must be inside its file.** Only a path
   whose first segment is a directory this repo HAS is a claim about this repo; a bare
   `routine.yaml` or `state/phase.json` names a file in a ROUTINE's directory, not in this
   tree, so it is not checked here — 413 of the ~975 path-shaped tokens in these guides are
   that bare shape, and `state/` alone accounts for 54 of the rest.

What is deliberately NOT checked — stated, never silent:

* **Whether a cited line still contains what the prose says it does.** That is judgement about
  prose. These checks are existence, and length as the citation is written.
* `docs/designs.md` is the decided-work QUEUE, not a guide: its entries describe code that does
  not exist yet (`engine/exitchecks.py`) and are deleted as they ship, so checking its symbols
  and paths against today's source would red the gate for the queue doing its job. Its own
  counts are still checked, and so are line citations into files that exist.
* `docs/status-pages.md` documents the Steward hub, a PHP/JS codebase deployed from another
  repository (`store.php`, `_shared/steward.js`, `web/steward/CONTRACT.md`): its symbols and
  paths are real and simply not here.
* `NOT_IN_THIS_REPO` names each remaining token that belongs to a foreign system, with its
  reason — `test_the_allowlist_has_not_gone_stale` reds if the source ever grows one, so an
  exemption cannot outlive the reason it was given.

Every pass asserts it FOUND something (`..._is_not_vacuous`): a prose scanner whose regex
silently stops matching passes while proving nothing, which is the one failure shape a file like
this must not have.
"""

import re
from functools import lru_cache
from pathlib import Path

from rsched.engine.actionschema import KINDS

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

#: Where a claim can be true: the code this repo ships, the tests that gate it, the seeded
#: library (rules, workflows, patterns and permissions carry the slugs and keys guides quote)
#: and the build/runtime configuration (`CAP_SYS_ADMIN` lives in docker-compose.yml, not in
#: Python). A token absent from ALL of these is absent from the system.
#: `deploy/` is in it because the host scripts and the desktop broker ARE shipped source: the
#: first run of this check called `DESKTOP_HOME_GB` (desktop-sessions.md) a drift when
#: `deploy/desktop-broker/vms.py:59` reads it. THIS module is excluded from the universe
#: (`_source_words`): a check whose allowlist proves its own tokens exist checks nothing.
SOURCE_GLOBS = ("src/**/*.py", "static/**/*.js", "static/**/*.css", "tests/**/*.py",
                "library-seed/**/*", "util-seed/**/*", "config/**/*", "deploy/**/*",
                "pyproject.toml", "Dockerfile", "docker-compose.yml",
                ".pre-commit-config.yaml")

#: Guides whose SUBJECT is not this repo's current source, with the reason. They stay in the
#: count pass and in the line-citation pass; they leave the symbol and path-existence passes.
UNCHECKED_SUBJECT = {
    "designs.md": "the decided-work queue: its entries name code that does not exist yet",
    "status-pages.md": "the Steward hub, a PHP/JS codebase deployed from another repository",
}

#: Tokens a guide quotes that belong to a foreign system — an allowlist with a stated reason,
#: never a silent exemption. Each one was checked against the whole tree before landing here.
NOT_IN_THIS_REPO = {
    "encrypted_key": "a key in Chrome's own `Local State` file (browser-sessions.md)",
    "logged_out": "a flag every `job-inbox` source reports — the util library, not this repo",
    "walk_files": "a function inside the `code-search` util, which lives in the util library",
    "feedback_cursor": "a Steward-hub state key, quoted in curated-rules.md's provenance table",
    "wall_clock_min": "quoted as the tail of the budget key `max_wall_clock_min`, in a list of "
                      "ceilings ('max_turns / total_tokens / wall_clock_min')",
    "frontier_llm": "a safety-classifier category an upstream API returns in `stop_details`",
    "reasoning_extraction": "a safety-classifier category an upstream API returns",
    "cache_ttl": "a per-model knob endpoints.md argues AGAINST having; absent by decision",
    "lossless_only": "a flag of headroom-ai's `SmartCrusher`, named in order to call it inert",
    "NNTP_SERVER": "a secret NAME in the central store, read by the `usenet` util",
    "NNTP_FROM": "a secret NAME in the central store, read by the `usenet` util",
}

_TICKED = re.compile(r"`([^`\n]{2,60})`")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ALLCAPS = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_SNAKE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
_DOTTED = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
_SUFFIXES = ("py", "js", "css", "md", "json", "toml", "yaml", "yml", "html", "sh", "php", "txt",
             "svg", "lock", "cfg", "ini", "xml", "csv", "jsonl")
#: A path-shaped token, with the line number when the prose cites one.
_PATHCITE = re.compile(r"`([A-Za-z_][\w./-]*\.(?:" + "|".join(_SUFFIXES) + r"))(?::(\d+))?`")
#: Where a doc's relative path can be rooted: the repo itself, the package (`rsched/x.py`), the
#: package's own namespace (`engine/actions.py`), `static` (`views/run.js`) and `tests`
#: (`ui/test_flows.py`) — the five spellings these guides actually use.
_ROOTS = ("", "src", "src/rsched", "static", "tests")


def _seed(kind: str) -> int:
    return len(list((ROOT / "library-seed" / kind).glob("*.md")))


#: (label, pattern, the number the prose must equal, why the pattern is this wide or narrow).
#: Each pattern is searched against the WHOLE document, not line by line, because these guides
#: wrap mid-sentence — a pattern spanning more than one word spells its gaps `\s+` for that
#: reason, as the permissions entry does.
COUNTS = (
    ("rules in library-seed/rules/", re.compile(r"\b(\d+)[ -]rules\b"), lambda: _seed("rules"),
     ("a guide saying 'N rules' means the shipped set; the corpus has no other sense for the "
      "phrase, so a new site anywhere is checked the moment someone writes it")),
    ("docs in library-seed/permissions/",
     re.compile(r"\b(\d+)\s+docs,\s+one file each in\s+`library-seed/permissions/`"),
     lambda: _seed("permissions"),
     "'N docs' alone is ambiguous, so this entry carries the sentence it belongs to"),
    ("action kinds in actionschema.KINDS", re.compile(r"\b(\d+)-kind schema\b"),
     lambda: len(KINDS), "the projection's own summary of the schema a run is given"),
)


@lru_cache(maxsize=1)
def _source_words() -> frozenset[str]:
    """Every identifier-shaped word in the source universe."""
    words: set[str] = set()
    me = Path(__file__).resolve()
    for pattern in SOURCE_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file() and path.resolve() != me:
                words |= set(_WORD.findall(path.read_text(encoding="utf-8", errors="replace")))
    return frozenset(words)


@lru_cache(maxsize=1)
def _guides() -> tuple[tuple[str, str], ...]:
    return tuple((p.name, p.read_text(encoding="utf-8")) for p in sorted(DOCS.glob("*.md")))


@lru_cache(maxsize=1)
def _repo_segments() -> frozenset[str]:
    """First path segments that mean THIS repo — derived, so a new package is covered at once."""
    segs = {"src", "static", "tests", "docs", "deploy", "library-seed", "util-seed", "config"}
    for base in (ROOT / "src", ROOT / "src" / "rsched", ROOT / "static", ROOT / "tests"):
        if base.is_dir():
            segs |= {p.name for p in base.iterdir()
                     if p.is_dir() and not p.name.startswith((".", "_"))}
    return frozenset(segs)


def _resolve(raw: str) -> list[str]:
    """Repo-relative paths a doc's citation can mean, in the spellings the guides use."""
    hits = [str((ROOT / root / raw).relative_to(ROOT))
            for root in _ROOTS if (ROOT / root / raw).is_file()]
    if hits or "/" in raw:
        return hits
    return sorted(str(p.relative_to(ROOT)) for base in ("src", "static", "tests", "docs")
                  for p in (ROOT / base).rglob(raw) if p.is_file())


def _normalise(token: str) -> str:
    """The claim inside the spellings these guides use for a symbol.

    Two of them are not the bare name, and both were found by perturbing a real claim and
    watching this file stay green:

    * `kindsurface.schema_for_kinds()` — the parens are prose, not part of the name, and
      treating them as part of it hid every function reference;
    * `engine/actions.BACKGROUNDABLE_KINDS` — the commonest spelling in these guides locates
      the symbol by FILE, and rejecting anything with a `/` hid the whole module-qualified
      class. The path half is the path pass's business; the symbol half is this one's.
    """
    return token.removesuffix("()").rsplit("/", 1)[-1]


def _is_symbol(token: str) -> bool:
    if "." in token and token.rsplit(".", 1)[-1] in _SUFFIXES:
        return False                      # a path — the path pass owns it
    return bool(_ALLCAPS.match(token) or _SNAKE.match(token) or _DOTTED.match(token))


def _symbol_claims() -> list[tuple[str, int, str]]:
    """(doc, line, token) per backticked token in a guide that claims a symbol exists."""
    found = []
    for name, text in _guides():
        if name in UNCHECKED_SUBJECT:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for raw in _TICKED.findall(line):
                token = _normalise(raw.strip())
                if _is_symbol(token):
                    found.append((name, lineno, token))
    return found


def _path_claims(line_cites: bool) -> list[tuple[str, int, str, str]]:
    """(doc, line, path, cited-line) for path-shaped tokens — citations, or plain paths."""
    found = []
    for name, text in _guides():
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in _PATHCITE.finditer(line):
                raw, cited = match.group(1), match.group(2) or ""
                if line_cites != bool(cited):
                    continue
                if not line_cites and (name in UNCHECKED_SUBJECT
                                       or raw.split("/")[0] not in _repo_segments()):
                    continue
                found.append((name, lineno, raw, cited))
    return found


def test_every_count_claim_in_a_guide_matches_its_source_collection() -> None:
    """A count is the cheapest documentation to falsify and the most expensive to disbelieve."""
    wrong = []
    for label, pattern, counter, _why in COUNTS:
        actual = counter()
        for name, text in _guides():
            for match in pattern.finditer(text):
                if int(match.group(1)) != actual:
                    line = text[:match.start()].count("\n") + 1
                    wrong.append(f"docs/{name}:{line} says {match.group(0)!r} — "
                                 f"{label} holds {actual}")
    assert not wrong, ("A guide states a count its source contradicts. Fix the PROSE to the "
                       "source (and sweep the other surfaces repeating the same claim):\n  "
                       + "\n  ".join(wrong))


def test_the_count_registry_is_not_vacuous() -> None:
    """Each entry must still match live prose, or it checks nothing and says it checked."""
    whole = "\n".join(text for _n, text in _guides())
    unmatched = [label for label, pattern, _c, _w in COUNTS if not pattern.search(whole)]
    assert not unmatched, (
        f"count entries that match no guide any more: {unmatched}. Either the prose was "
        "rewritten (re-point the pattern) or the claim is gone (drop the entry) — a pattern "
        "that matches nothing passes while proving nothing.")
    anatomy = dict(_guides())["prompt-anatomy.md"]
    assert COUNTS[2][1].search(anatomy), "the kind-count claim is no longer in prompt-anatomy.md"
    assert f"{len(KINDS)}-kind schema" in anatomy, (
        f"prompt-anatomy.md should read '{len(KINDS)}-kind schema' — this pins the pass to a "
        "claim a reader can check by hand")


def test_every_backticked_identifier_still_exists_in_the_source() -> None:
    """A renamed symbol leaves a guide quoting a name the system no longer answers to."""
    words = _source_words()
    missing = []
    for name, lineno, token in _symbol_claims():
        if token in NOT_IN_THIS_REPO:
            continue
        absent = [part for part in token.split(".") if part not in words]
        if absent:
            missing.append(f"docs/{name}:{lineno} `{token}` — not in the source ({absent})")
    assert not missing, (
        "A guide quotes an identifier the source does not have. Fix the PROSE to the source, "
        "or add the token to NOT_IN_THIS_REPO with the reason it is not ours:\n  "
        + "\n  ".join(missing))


def test_the_identifier_finder_is_not_vacuous() -> None:
    """Pinned so a broken regex cannot quietly empty the pass above."""
    claims = _symbol_claims()
    tokens = {token for _n, _l, token in claims}
    assert len(claims) > 1500, f"only {len(claims)} identifier claims found — did `_TICKED` break?"
    # One per SPELLING, not per symbol: a bare name, a module-qualified one (written with the
    # file, `engine/actions.BACKGROUNDABLE_KINDS`) and a function call with its parens. Each
    # of the last two was a whole claim class this finder silently dropped once.
    for known in ("BACKGROUNDABLE_KINDS", "DEFAULT_RULES", "kindsurface.schema_for_kinds",
                  "actions.BACKGROUNDABLE_KINDS"):
        assert known in tokens, (f"`{known}` is quoted in a guide but the finder missed it — "
                                 "the extraction regexes no longer see a whole claim class")
    assert "KIND_PROSE" in _source_words(), "the source universe no longer reaches kindprose.py"


def test_every_path_a_guide_cites_resolves_to_a_file() -> None:
    """`engine/actions.py` in prose is a claim that the file is where the doc says it is."""
    missing = []
    for name, lineno, raw, _cited in _path_claims(line_cites=False):
        if not _resolve(raw):
            missing.append(f"docs/{name}:{lineno} `{raw}`")
    assert not missing, (
        "A guide cites a path this repo does not have (first segment is one of its own "
        "directories, so it is a claim about this tree). Fix the PROSE:\n  "
        + "\n  ".join(missing))


def test_every_line_citation_points_inside_its_file() -> None:
    """`base.py:259` claims the file has at least 259 lines. Whether line 259 still SAYS what
    the prose says it does is judgement, and is not checked (see the module docstring)."""
    bad = []
    for name, lineno, raw, cited in _path_claims(line_cites=True):
        hits = _resolve(raw)
        if not hits:
            bad.append(f"docs/{name}:{lineno} cites `{raw}:{cited}` — no such file")
            continue
        lengths = {hit: len((ROOT / hit).read_text(encoding="utf-8").splitlines())
                   for hit in hits}
        if not any(length >= int(cited) for length in lengths.values()):
            bad.append(f"docs/{name}:{lineno} cites `{raw}:{cited}`, past the end of "
                       f"every candidate: {lengths}")
    assert not bad, ("A line citation points outside its file — re-point it or drop the line "
                     "number:\n  " + "\n  ".join(bad))


def test_the_path_finder_is_not_vacuous() -> None:
    """Both path passes pinned to citations a reader can verify by hand."""
    paths = {raw for _n, _l, raw, _c in _path_claims(line_cites=False)}
    assert len(paths) > 150, f"only {len(paths)} distinct in-repo paths found — regex broken?"
    for known in ("engine/actions.py", "src/rsched/docs_build.py", "views/help.js"):
        assert known in paths or _resolve(known), f"`{known}` no longer resolves"
    assert "engine/actions.py" in paths, "the path finder no longer sees the commonest spelling"
    cites = _path_claims(line_cites=True)
    assert cites, ("no `path:line` citation found in docs/ — there were 2 when this check was "
                   "written; if the prose dropped them, this pass now proves nothing")


def test_the_allowlist_has_not_gone_stale() -> None:
    """An exemption may not outlive its reason: a token the source now HAS must leave the list,
    and one no guide quotes any more is dead weight."""
    words = _source_words()
    now_ours = sorted(token for token in NOT_IN_THIS_REPO if token in words)
    assert not now_ours, (f"NOT_IN_THIS_REPO exempts tokens the source now has: {now_ours}. "
                          "Drop them — they are checkable claims again.")
    quoted = {token for _n, _l, token in _symbol_claims()}
    unquoted = sorted(token for token in NOT_IN_THIS_REPO if token not in quoted)
    assert not unquoted, (f"NOT_IN_THIS_REPO names tokens no checked guide quotes: {unquoted}. "
                          "Drop them, so the list stays a record of live exemptions.")
