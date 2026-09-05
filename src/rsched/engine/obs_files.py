"""Observation wording for the FILE and curated-store kinds.

Split out of `observations.py` (F393). These are the run's ordinary senses, so their wording
carries the things a model gets wrong without them: which paths a batched read actually
returned, that a write was grounded (or why it was refused), the anchor that failed to match on
an edit, and — for `.memory/` and the rule library — that the engine owns the index.
"""

from __future__ import annotations


def format_files(obs: dict, kind: str) -> str | None:  # noqa: C901, PLR0911, PLR0912 — one flat renderer per module, by design: observation wording is PROMPT SURFACE (docs/prompt-anatomy.md) and every branch is a distinct string for a distinct kind. Collapsing them would scatter a kind's wording, which is exactly what this shape exists to prevent.
    """Wording for this module's kinds; None when `kind` is not one of them."""
    if kind == "read_file":
        if obs.get("files") is not None:  # batched multi-path read
            parts = []
            for f in obs["files"]:
                if f.get("error"):
                    parts.append(f"--- {f['path']} FAILED: {f['error']}")
                else:
                    parts.append(f"--- {f['path']} (lines {f['start_line']}-{f['end_line']} "
                                 f"of {f['total_lines']}) ---\n{f['content']}")
            return f"OBSERVATION (read_file, {len(obs['files'])} files):\n" + "\n\n".join(parts)
        if err := obs.get("error"):
            return f"OBSERVATION (read_file {obs.get('path')} FAILED): {err}"
        return (f"OBSERVATION (read_file {obs['path']}, lines "
                f"{obs['start_line']}-{obs['end_line']} of {obs['total_lines']}):\n"
                f"{obs['content']}")
    if kind == "view_image":
        parts = []
        for f in obs.get("files", []):
            if f.get("error"):
                parts.append(f"--- {f['path']} FAILED: {f['error']}")
            elif f.get("native"):
                parts.append(f"--- {f['path']} ({f['media_type']}) — shown to you below; "
                             "look at it now.")
            elif f.get("via") == "vision-util":
                parts.append(f"--- {f['path']} (described by the vision util — this run's model "
                             f"can't view it directly):\n{f.get('text', '')}")
            else:
                parts.append(f"--- {f['path']}: (no result)")
        head = ("OBSERVATION (view_image — image(s) attached below for you to see):"
                if obs.get("media") else "OBSERVATION (view_image):")
        return head + "\n" + "\n\n".join(parts)
    if kind == "write_file":
        if err := obs.get("error"):
            return f"OBSERVATION (write_file {obs.get('path')} FAILED): {err}"
        base = f"OBSERVATION (write_file): wrote {obs['bytes']} bytes to {obs['path']}"
        if obs.get("append"):
            size = obs.get("size")
            # show the resulting total so a silent overwrite (size == bytes) is visible
            return base + (f" (appended; file now {size} bytes)" if size is not None
                           else " (appended)")
        return base
    if kind == "edit_file":
        if err := obs.get("error"):
            return f"OBSERVATION (edit_file {obs.get('path')} FAILED): {err}"
        return (f"OBSERVATION (edit_file): replaced {obs['replacements']} occurrence(s) in "
                f"{obs['path']} (now {obs['bytes']} bytes)")
    if kind == "memory_read":
        if obs.get("missing"):
            topics = ", ".join(obs.get("topics") or []) or "(none yet)"
            return (f"OBSERVATION (memory_read): no note named {obs['name']!r}. "
                    f"Existing topics: {topics}.")
        return (f"OBSERVATION (memory_read {obs['name']}.md, {obs['lines']} lines):\n"
                f"{obs['content']}")
    if kind == "read_rule":
        if obs["name"] == "list":
            rows = "\n".join(f"- {r['slug']}{' (binds you)' if r['held'] else ''}: "
                             f"{r['summary']}" for r in obs["rules"]) or "(library is empty)"
            return ("OBSERVATION (read_rule list) — general rules in the shared library. One you "
                    "do not hold applies to THIS run only; which rules bind you is the user's "
                    f"call:\n{rows}")
        if obs.get("missing"):
            avail = ", ".join(obs.get("available") or []) or "(none)"
            return (f"OBSERVATION (read_rule): no rule named {obs['name']!r}. "
                    f"Available: {avail}.")
        binds = (" — this rule BINDS you" if obs.get("held")
                 else " — you do not hold this rule; it applies for the rest of this run only")
        return (f"OBSERVATION (read_rule {obs['name']}, {obs['lines']} lines{binds}). "
                "It states a principle: apply it to the case in front of you.\n"
                f"{obs['content']}")
    if kind == "memory_write":
        if obs.get("deleted"):
            fate = ("deleted and INDEX updated" if obs.get("existed")
                    else "did not exist — nothing to delete")
            return f"OBSERVATION (memory_write): note {obs['name']}.md {fate}."
        return (f"OBSERVATION (memory_write): note {obs['name']}.md "
                f"{'created' if obs.get('created') else 'revised'} ({obs['lines']} lines); "
                "INDEX.md updated from 'about'.")
    return None
