"""Create a library workflow on demand: one LLM draft, lint-gated with one repair round,
saved as status: draft and committed."""

from __future__ import annotations

from pathlib import Path

from ..config import ServerConfig
from ..endpoints import EndpointRegistry
from ..ids import slugify
from .library import git_commit, list_fragments, read_workflow, workflows_dir
from .lint import lint_workflow_text

FORMAT_SPEC = """A library workflow file is markdown with YAML frontmatter:
---
name: <Human name>
slug: <kebab-case, becomes the filename>
description: <one line: what this workflow does>
when_to_use: >
  <2-4 sentences a matcher uses to pair instructions with this workflow>
version: 1
status: draft
params: []            # optional {{placeholder}} names filled at adaptation
default_budgets: {max_turns: 60, max_wall_clock_min: 45}
requires: {schema_output: false}
includes: [ask-policy, global-utils, ledger-discipline, improve-bugfix, improve-research, improve-features, improve-ui, improve-efficiency]
---
Body sections, all three REQUIRED:
## Run flow — numbered natural-language steps for ONE run (the orchestrator LLM follows
them literally; tools are `gu` utils via shell, read_file/write_file, llm subcalls,
spawn/subruns/kill/wait for parallel library sub-workflows, ask_user, finish).
## Phases — how the routine progresses across runs toward final delivery (persist the
current phase in state/phase.json), or "- **steady** — no cross-run milestones."
## Completion criteria — what "done for this run" and "done overall" mean."""


def generate(server: ServerConfig, instruction: str, hint: str = "") -> tuple[str, str]:
    """Draft a new workflow for the instruction. Returns (slug, problems_note); the file is
    written + committed on success. Raises RuntimeError when the draft can't be made valid."""
    home = server.library_home
    frags = list_fragments(home)
    _, example_body, example_raw = read_workflow(home, "general-task")
    prompt = (
        "Draft ONE new workflow file for the workflow library, for recurring instructions "
        f"shaped like this one:\n\nINSTRUCTION:\n{instruction}\n\n"
        + (f"SHAPE HINT: {hint}\n\n" if hint else "")
        + f"{FORMAT_SPEC}\n\nAvailable fragments for `includes`: {frags}\n\n"
        f"A good existing workflow for reference:\n\n{example_raw}\n\n"
        "Requirements: generalize (the workflow is a PATTERN — the instruction stays "
        "separate), keep steps concrete and tool-oriented, status: draft, version: 1. "
        "Reply with ONLY the complete file content, starting with '---'."
    )
    endpoint, ref = EndpointRegistry(server).for_system()
    draft = endpoint.complete([{"role": "user", "content": prompt}],
                              model=ref.model, timeout=180).text.strip()
    if draft.startswith("```"):
        draft = draft.strip("`").lstrip("markdown").strip()

    for attempt in range(2):
        slug = _slug_of(draft) or slugify(instruction[:40])
        problems = lint_workflow_text(draft, filename=f"{slug}.md", fragment_slugs=frags)
        if not problems:
            path = workflows_dir(home) / f"{slug}.md"
            if path.exists():
                slug = f"{slug}-2"
                draft = draft.replace(f"slug: {_slug_of(draft)}", f"slug: {slug}", 1)
                path = workflows_dir(home) / f"{slug}.md"
            path.write_text(draft.rstrip() + "\n", encoding="utf-8")
            git_commit(home, f"draft workflow {slug} (generated on demand)")
            return slug, ""
        if attempt == 0:
            fix = endpoint.complete([{"role": "user", "content":
                f"This workflow file failed lint:\n{draft}\n\nProblems:\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\n\nReply with ONLY the corrected complete file content."}],
                model=ref.model, timeout=180)
            draft = fix.text.strip().strip("`").lstrip("markdown").strip()
    raise RuntimeError(f"generated workflow failed lint twice: {problems}")


def _slug_of(draft: str) -> str:
    from .. import frontmatter

    meta, _ = frontmatter.parse(draft)
    return str(meta.get("slug", "")) if isinstance(meta, dict) else ""
