"""The conversation playbook seeds the census asked for, each checked as a SHIPPABLE brief.
`test_seeds.py` parametrizes over whatever `library-seed/playbooks/*/MAIN.md` happens to hold, so
it proves a seed that exists is well formed and says nothing about a seed that is missing. These
cases name the chosen slugs, so dropping one is a red rather than a quietly shorter catalog, and
they assert the three properties a brief is useless without: a setup declaration the library
linter passes, a `when`/`axis` pair that is nobody else's, and parameters of its own. The
AI-detection brief carries one further case: it is written for reproducible EVALUATION of
detectors, and prose that reads as evasion guidance fails here rather than in review.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest
import yaml

from conftest import seeded_server
from rsched import library_docs, playbooks
from rsched.entities import guarded_roots
from rsched.workflows.lint import lint_playbook_text

REPO = Path(__file__).resolve().parents[1]
LIBRARY_SEED = REPO / "library-seed"
PLAYBOOK_DIR = LIBRARY_SEED / "playbooks"

#: The slugs chosen for shipping; each must be a seeded directory holding a MAIN.md.
CHOSEN = ("grant-latex-writing", "ai-detection-evaluation", "research-paper-work")
#: Above this ratio two catalog lines read as the same brief filed twice.
SAME_BRIEF = 0.60
#: Evasion vocabulary: an AI-detection brief that helps text get past a detector instead of
#: measuring one. Matched on the lowercased body, so it catches the phrasing as well as the word.
EVASION = ("evade detect", "evade a detect", "avoid detect", "bypass detect", "defeat detect",
           "beat the detect", "humanize", "humanise", "undetectable", "fool the detect",
           "lower the ai score", "reduce the ai score", "rewrite until", "paraphrase until")
#: What a reproducible evaluation brief has to talk about at all. Matched against the body with
#: hyphens flattened to spaces, because "false-positive rate" and "a false positive" are the same
#: subject and a test has no business dictating which one the prose uses.
EVALUATION_TERMS = ("held out", "reproduc", "uncertaint", "false positive", "threshold")


def _main(slug: str) -> Path:
    return PLAYBOOK_DIR / slug / "MAIN.md"


def _parts(slug: str) -> tuple[dict, str]:
    """`(frontmatter, body)` of one seeded playbook — the shape every case here reads."""
    raw = _main(slug).read_text(encoding="utf-8")
    head, body = raw.split("---", 2)[1], raw.split("---", 2)[2]
    return yaml.safe_load(head) or {}, body


def _all_seeded() -> list[str]:
    return sorted(p.parent.name for p in PLAYBOOK_DIR.glob("*/MAIN.md"))


# ---- the slugs exist, and the library linter passes each one ------------------------------


@pytest.mark.parametrize("slug", CHOSEN)
def test_a_chosen_playbook_seed_is_a_directory_with_a_main(slug):
    """One directory per playbook holding a single MAIN.md is the layout the boot-time seed sync
    installs; a file dropped beside the directories reaches no instance."""
    assert _main(slug).is_file(), (
        f"library-seed/playbooks/{slug}/MAIN.md is missing — seeded playbooks are "
        f"{_all_seeded()}")


@pytest.mark.parametrize("slug", CHOSEN)
def test_a_chosen_playbook_seed_lints_clean(slug):
    """The acceptance wording is a linter-valid declaration, so the linter is what judges it."""
    problems = lint_playbook_text(_main(slug).read_text(encoding="utf-8"),
                                  filename=f"{slug}/MAIN.md")
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("slug", CHOSEN)
def test_a_chosen_playbook_frontmatter_has_the_catalog_shape(slug):
    """`tags` as a STRING has been a real defect here — iterated one tag per character — so the
    list-ness is asserted, not assumed, along with the slug↔directory agreement the store keys
    off and the single-line `when` the picker renders."""
    meta, _ = _parts(slug)
    assert meta.get("slug") == slug, f"{slug}: frontmatter slug {meta.get('slug')!r}"
    assert isinstance(meta.get("tags"), list), f"{slug}: tags must be a list, not a string"
    assert len([t for t in meta["tags"] if len(str(t).strip()) > 1]) >= 3, (
        f"{slug}: needs at least 3 real tags, got {meta['tags']!r}")
    assert "\n" not in str(meta.get("when") or "").strip(), f"{slug}: `when` must be one line"
    assert str(meta.get("axis") or "").strip(), f"{slug}: no axis"
    assert str(meta.get("title") or "").strip(), f"{slug}: no title"
    assert str(meta.get("updated") or "").strip(), f"{slug}: no updated stamp"


# ---- the setup declaration -----------------------------------------------------------------


@pytest.mark.parametrize("slug", CHOSEN)
def test_a_chosen_playbook_declares_the_setup_a_conversation_needs(slug):
    """The brief cannot say what a conversation made from it must be GIVEN; the `setup:` block
    is where that is readable before reply one. Every permission and rule it names must be a
    seed doc that exists, no root may be a credential store, and the note has to say something a
    person can act on."""
    meta, _ = _parts(slug)
    setup = meta.get("setup")
    assert isinstance(setup, dict), f"{slug}: setup: must be a mapping, got {type(setup)}"
    perms = set(library_docs.slugs(LIBRARY_SEED / "permissions"))
    rules = set(library_docs.slugs(LIBRARY_SEED / "rules"))
    unknown = ([f"permission {p!r}" for p in (setup.get("permissions") or []) if p not in perms]
               + [f"rule {r!r}" for r in (setup.get("rules") or []) if r not in rules])
    assert not unknown, f"{slug}: setup: names {', '.join(unknown)} — no such seed doc"
    assert setup.get("rules"), f"{slug}: setup: holds no rules"
    roots = [r for r in (*(setup.get("fs_read_roots") or []),
                         *(setup.get("fs_write_roots") or []),
                         *([setup["workdir"]] if setup.get("workdir") else []))
             if not str(r).startswith("{{")]
    assert guarded_roots(roots) == [], f"{slug}: setup: grants a guarded root"
    assert len(str(setup.get("note") or "").strip()) >= 80, (
        f"{slug}: setup.note says too little to act on")


# ---- distinctness: a brief nobody else is already --------------------------------------------


def test_each_chosen_brief_is_distinct_from_every_other_seeded_playbook():
    """Three near-copies are one playbook listed three times: the picker shows `when` and `axis`,
    so those lines are what has to differ, measured rather than eyeballed."""
    lines = {slug: (str(_parts(slug)[0].get("when") or ""), str(_parts(slug)[0].get("axis") or ""))
             for slug in _all_seeded()}
    clashes = []
    for slug in CHOSEN:
        for other, (when, axis) in lines.items():
            if other == slug:
                continue
            for field, mine, theirs in (("when", lines[slug][0], when),
                                        ("axis", lines[slug][1], axis)):
                ratio = difflib.SequenceMatcher(None, mine.lower(), theirs.lower()).ratio()
                if ratio >= SAME_BRIEF:
                    clashes.append(f"{slug}.{field} is {ratio:.2f} similar to {other}.{field}")
    assert not clashes, "\n".join(clashes)


@pytest.mark.parametrize("slug", CHOSEN)
def test_a_chosen_brief_names_its_own_parameters(slug):
    """The axis is only real if the body turns it into placeholders, and a playbook whose every
    parameter is already another one's is not generalizing over anything of its own."""
    import re

    def params(s: str) -> set[str]:
        return set(re.findall(r"\{\{([a-z_]+)\}\}", _parts(s)[1]))

    mine = params(slug)
    assert len(mine) >= 3, f"{slug}: declares only {sorted(mine)} as parameters"
    shared = set().union(*(params(o) for o in _all_seeded() if o != slug))
    assert mine - shared, f"{slug}: every parameter {sorted(mine)} is another playbook's too"


# ---- the AI-detection brief is an EVALUATION brief -------------------------------------------


def test_the_ai_detection_brief_measures_detectors_and_never_helps_text_evade_them():
    """The operator's explicit constraint on this seed: it is for reproducible evaluation of
    detectors — held-out material, documented prompts, a fixed scoring procedure, reported
    uncertainty — and not for getting text past one. Evasion phrasing fails here rather than in
    review, including in a later revision nobody re-reads with the constraint in mind."""
    meta, body = _parts("ai-detection-evaluation")
    text = (str(meta.get("title")) + " " + str(meta.get("when")) + " "
            + str(meta.get("axis")) + " " + body).lower().replace("-", " ")
    found = [phrase for phrase in EVASION if phrase in text]
    assert not found, (f"ai-detection-evaluation reads as evasion guidance: {found} — the brief "
                       "is for measuring detectors, not for defeating them")
    missing = [term for term in EVALUATION_TERMS if term not in text]
    assert not missing, (f"ai-detection-evaluation never mentions {missing} — a reproducible "
                         "evaluation brief has to say how the measurement holds up")


# ---- the catalog a new conversation is offered ------------------------------------------------


def test_the_seeded_library_serves_all_three_playbooks(tmp_path):
    """Served, not merely on disk: the picker reads the library catalog, and a seed the store
    does not list is a brief nobody is offered."""
    server = seeded_server(tmp_path, kinds=("workflows", "rules", "permissions", "playbooks"))
    served = {p["slug"]: p for p in playbooks.list_playbooks(server.libraries_home)}
    for slug in CHOSEN:
        assert slug in served, f"{slug} is not in the served catalog {sorted(served)}"
        assert str(served[slug].get("title") or "").strip(), f"{slug}: served with no title"
        assert str(served[slug].get("when") or "").strip(), f"{slug}: served with no when"
