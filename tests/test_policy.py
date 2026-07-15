"""Machine-checked repo policies — conventions that broke silently once are tests now.

1. **Delete-after-convergence** (migrations): one-shot migration code must declare its own
   expiry with a `MIGRATION(expires=YYYY-MM-DD)` marker comment next to the code, and the
   suite FAILS once that date passes — a "temporary" migration can never silently become
   permanent. Zero migrations exist today; this guard is for the next one.
2. **Version discipline**: a bump of `rsched.__version__` must come with a matching
   `## [x.y.z]` header at the top of CHANGELOG.md (0.27 shipped without notes once).
   A pre-commit hook runs this file so the mismatch never reaches a commit.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import rsched

SRC = Path(rsched.__file__).parent
REPO = Path(__file__).resolve().parent.parent

MIGRATION_MARKER = re.compile(r"MIGRATION\(expires=(\d{4}-\d{2}-\d{2})\)")
# code that LOOKS like a migration: a migrate-named def/class or a migrate-* CLI command
MIGRATION_CODE = re.compile(r"def \w*migrat\w*|class \w*[Mm]igrat\w*|[\"']migrate-")


def test_migration_code_declares_expiry_and_expires():
    today = datetime.now(tz=UTC).date().isoformat()
    problems = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        markers = MIGRATION_MARKER.findall(text)
        problems.extend(
            f"{path.relative_to(REPO)}: migration expired {expires} — it has converged "
            "on production; DELETE the migration code (the delete-after-convergence "
            "policy, see CLAUDE.md)"
            for expires in markers if expires < today)
        if MIGRATION_CODE.search(text) and not markers:
            problems.append(
                f"{path.relative_to(REPO)}: migration-shaped code without a "
                "MIGRATION(expires=YYYY-MM-DD) marker — every one-shot migration must "
                "declare when it is overdue for deletion")
    assert not problems, "\n".join(problems)


def test_version_bump_has_changelog_entry():
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    headers = re.findall(r"^## \[(\d+\.\d+\.\d+)\] — (\d{4}-\d{2}-\d{2})$",
                         changelog, flags=re.MULTILINE)
    assert headers, "CHANGELOG.md has no '## [x.y.z] — YYYY-MM-DD' release header"
    assert headers[0][0] == rsched.__version__, (
        f"__version__ is {rsched.__version__} but the newest CHANGELOG.md header is "
        f"[{headers[0][0]}] — every version bump ships its release notes in the same commit")
