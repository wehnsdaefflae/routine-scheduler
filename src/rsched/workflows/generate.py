"""Create a library workflow on demand: one LLM draft, lint-gated with one repair round,
saved as status: draft and committed. Workflows are Python-pattern files (`.py`)."""

from __future__ import annotations


from ..config import ServerConfig
from ..endpoints import EndpointRegistry
from ..ids import slugify
from .library import git_commit, list_traits, read_workflow, workflows_dir
from .lint import lint_workflow_py

FORMAT_SPEC = '''A library workflow is a single self-contained Python file (.py) that DEPICTS a
routine's control flow — it is NEVER executed. Structure:

"""<module docstring: what the workflow does, and that it is a PATTERN acted out one action per turn>"""

# Dummy parameter imports — each NAMES one piece of information the clarifier fixes for the concrete
# task; the trailing comment gives its type and meaning. They never resolve at run time.
from routine.params import (
    PARAM_ONE,    # <type> — <what it means>
    PARAM_TWO,    # <type> — <what it means>
)
from routine.actions import read_file, write_file, util, write_util, llm, spawn, wait, ask_user, finish
from routine.state import phase, ledger

META = {
    "name": "<Human name>", "slug": "<kebab-case, equals the filename>",
    "description": "<one line: what this workflow does>",
    "when_to_use": "<2-4 sentences a matcher uses to pair instructions with this workflow>",
    "version": 1, "status": "draft",
    "tags": [<at least 3 tags>],
    "includes": [<trait slugs from the available list — the practice modules this pattern suggests>],
    "tools": None,      # or a list of allowed action kinds, e.g. ["read_file", "write_file", "finish"]
}
PHASES = [<cross-run phases>]       # or ["steady"] when there are no cross-run milestones
COMPLETION = "<done-for-this-run; done-overall>"

def main():
    """The per-run control flow. Use real Python — if/elif/else, for/while, try/except, match — and
    call the engine actions. Keep it a PATTERN (generic to the shape of task), not one task's
    specifics."""
    ...

# Define one function per step; each function's docstring is that step's detail.

if __name__ == "__main__":
    main()

META / PHASES / COMPLETION must be plain literals (they are parsed statically with ast, never run).
Use the full range of Python control flow wherever it makes the process clearer.'''


def generate(server: ServerConfig, instruction: str, hint: str = "") -> tuple[str, str]:
    """Draft a new Python workflow for the instruction. Returns (slug, problems_note); the file is
    written + committed on success. Raises RuntimeError when the draft can't be made valid."""
    home = server.library_home
    traits = list_traits(home)
    _, _, example_raw = read_workflow(home, "general-task")   # a good Python workflow to imitate
    prompt = (
        "Draft ONE new workflow file for the workflow library, for recurring instructions "
        f"shaped like this one:\n\nINSTRUCTION:\n{instruction}\n\n"
        + (f"SHAPE HINT: {hint}\n\n" if hint else "")
        + f"{FORMAT_SPEC}\n\nAvailable traits for `includes`: {traits}\n\n"
        f"A good existing workflow for reference:\n\n{example_raw}\n\n"
        "Requirements: generalize (the workflow is a PATTERN — the instruction stays separate), "
        "depict the control flow with real Python, status: draft, version: 1. "
        "Reply with ONLY the complete .py file content."
    )
    endpoint, ref = EndpointRegistry(server).for_system()
    draft = _strip_fence(endpoint.complete([{"role": "user", "content": prompt}],
                                           model=ref.model, timeout=180).text)

    problems: list[str] = []
    for attempt in range(2):
        slug = _slug_of(draft) or slugify(instruction[:40])
        problems = lint_workflow_py(draft, filename=f"{slug}.py", trait_slugs=traits)
        if not problems:
            path = workflows_dir(home) / f"{slug}.py"
            if path.exists():
                slug = f"{slug}-2"
                path = workflows_dir(home) / f"{slug}.py"
            path.write_text(draft.rstrip() + "\n", encoding="utf-8")
            git_commit(home, f"draft workflow {slug} (generated on demand)")
            return slug, ""
        if attempt == 0:
            fix = endpoint.complete([{"role": "user", "content":
                f"This Python workflow file failed lint:\n{draft}\n\nProblems:\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\n\nReply with ONLY the corrected complete .py file content."}],
                model=ref.model, timeout=180)
            draft = _strip_fence(fix.text)
    raise RuntimeError(f"generated workflow failed lint twice: {problems}")


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        for lang in ("python", "py"):
            if text.lstrip().startswith(lang):
                text = text.lstrip()[len(lang):]
                break
    return text.strip()


def _slug_of(draft: str) -> str:
    from .pyworkflow import parse_py
    try:
        return str(parse_py(draft).get("slug", ""))
    except (SyntaxError, ValueError):
        return ""
