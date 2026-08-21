"""File-shaped effect handlers: read_file / view_image / write_file / edit_file, the
memory actions, and read_rule — plus the path gates they share (the runs/ read depth,
the write-grounding rule, the recipe/config seal). Split from executor.py, which keeps
dispatch, the util runner, and the llm subcall (and routes the file kinds here).
"""

from __future__ import annotations

import difflib
import json

from .. import sandbox, utils_lib
from ..endpoints.base import NATIVE_MEDIA_MAX_BYTES, guess_media_type
from ..paths import atomic_write, resolve_rel
from ..readmodels.statemap import STAGES_DIR
from .observations import OBS_CAP_CHARS, truncate
from .outputs import OUTPUTS_DIR
from .run_context import RunContext

READ_DEFAULT_MAX_LINES = 200
UTIL_DEFAULT_TIMEOUT_S = 300
VISION_UTIL = "vision"
VIEW_DEFAULT_PROMPT = ("Describe this file in full detail — transcribe any text verbatim and "
                       "note structure, data, and notable visual elements.")


def _runs_read_gate(ctx: RunContext, resolved) -> str | None:
    """Backstop for previous-run access (grants.deny handles the relative-path form inside
    the schema-retry cycle; this catches absolute paths and scopes `runs: last`). The
    current run's own tree — status, archived history — is always readable.
    """
    g = ctx.grants
    if g is None:
        return None
    runs_dir = ctx.routine.dir / "runs"
    try:
        rel = resolved.relative_to(runs_dir)
    except ValueError:
        return None
    if resolved.is_relative_to(ctx.root_run_dir):
        return None
    if g.run_history == "none":
        # after D96 a routine's own policy always carries at least "last" — this branch
        # serves scopes genuinely without history (sub-workflow children, synthetic
        # policies), so the copy must not claim the ROUTINE lacks a permission (R46).
        return ("previous runs are not readable in this scope — a routine reads its own "
                "last run by default, but sub-workflows run without run history; fold "
                "what the child needs into its brief instead")
    if g.run_history == "last":
        prior = sorted(d.name for d in runs_dir.iterdir()
                       if d.is_dir() and d.name != ctx.root_run_dir.name)
        last = prior[-1] if prior else None
        if not rel.parts or rel.parts[0] != last:
            return (f"previous runs are readable at the default 'last' depth only "
                    f"({'runs/' + last if last else 'none exists yet'}); the run-history "
                    f"permission raises the depth to 'all' for longitudinal work")
    return None


def _read_one(rel_path: str, action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_rel(ctx.routine.dir, rel_path, ctx.read_roots())
        if err := _runs_read_gate(ctx, path):
            return {"path": rel_path, "error": err}
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError) as exc:
        return {"path": rel_path, "error": str(exc)}
    ctx.seen_paths.add(str(path))
    # Reading a stage module IS the run's state transition — every recipe routes by
    # "read the module for where you are" — so the engine tracks the live phase right
    # here (→ status.json → the SSE state event) with zero recipe cooperation; the
    # stage modules are the state graph's nodes (statemap), so the names always match.
    if (path.suffix == ".md" and path.parent.name == STAGES_DIR
            and path.parent.parent == ctx.routine.dir):
        ctx.phase = path.stem
    lines = text.splitlines()
    start = max(1, int(action.get("start_line") or 1))
    max_lines = min(int(action.get("max_lines") or READ_DEFAULT_MAX_LINES), 500)
    window = lines[start - 1 : start - 1 + max_lines]
    end_line = min(start - 1 + max_lines, len(lines))
    content = "\n".join(window)
    truncated = False
    if len(content) > OBS_CAP_CHARS:
        # END-truncate a file read: keep whole lines from the HEAD, drop the tail, and report
        # the exact next start_line — so a follow-up read continues IN SEQUENCE. (Opaque output
        # rides truncate()'s head+tail elision, where the tail's tail — a traceback end — must
        # survive; a file read is ordered, so dropping the tail and resuming is the right shape.
        # Operator AUDIT note, F204.)
        kept: list[str] = []
        used = 0
        for ln in window:
            if kept and used + len(ln) + 1 > OBS_CAP_CHARS:
                break
            kept.append(ln)
            used += len(ln) + 1
        end_line = start + len(kept) - 1
        core = "\n".join(kept)
        if len(core) > OBS_CAP_CHARS:          # a single line longer than the cap
            core = core[:OBS_CAP_CHARS]
        content = (core + f"\n[... {len(kept)} of {len(window)} window lines shown (through line "
                   f"{end_line} of {len(lines)}); truncated at {OBS_CAP_CHARS} chars — re-read "
                   f"with start_line={end_line + 1} to continue in sequence ...]")
        truncated = True
    return {"path": rel_path, "start_line": start,
            "end_line": end_line, "total_lines": len(lines),
            "content": content, "truncated": truncated}


def do_read_file(action: dict, ctx: RunContext) -> dict:
    paths = action.get("paths")
    if paths:  # batched read: several files in ONE action, one entry each
        return {"kind": "read_file", "files": [_read_one(str(p), action, ctx) for p in paths]}
    return {"kind": "read_file", **_read_one(action["path"], action, ctx)}


def vision_describe(ctx: RunContext, abspath: str, prompt: str) -> str:
    """Run the `vision` util on one file and return its text (or an 'error: …' string). The
    single fallback used both by do_view_image and the loop's runtime net when the main
    endpoint can't take a file natively; the util bills its own key, out of the run's usage.
    """
    home = ctx.server.libraries_home
    if not utils_lib.exists(home, VISION_UTIL):
        return "error: the `vision` util is not installed, so this file cannot be described"
    args = [abspath, "--prompt", prompt or VIEW_DEFAULT_PROMPT, "--json"]
    code, out, err = utils_lib.run_util(home, VISION_UTIL, args, timeout=UTIL_DEFAULT_TIMEOUT_S,
                                        policy=sandbox.policy_for_ctx(ctx),
                                        cwd=ctx.routine.dir)
    if code != 0:
        return f"error: vision util failed (exit {code}): {(err or out or '').strip()[:800]}"
    try:
        return json.loads(out).get("text") or out
    except (json.JSONDecodeError, AttributeError):
        return out


def _view_via_vision(rel_path: str, abspath: str, prompt: str, ctx: RunContext) -> dict:
    text = vision_describe(ctx, abspath, prompt)
    if text.startswith("error:"):
        return {"path": rel_path, "via": "vision-util", "error": text[len("error:"):].strip()}
    text, truncated = truncate(text)
    return {"path": rel_path, "via": "vision-util", "text": text, "truncated": truncated}


def _view_one(rel_path: str, prompt: str, endpoint, ctx: RunContext, multimodal: bool) -> dict:
    """Route one file: native (return a media entry for the endpoint to see) when the main
    MODEL is multimodal, the endpoint supports the type, and it's within the native size cap,
    else the vision util.
    """
    try:
        path = resolve_rel(ctx.routine.dir, rel_path, ctx.read_roots())
        if err := _runs_read_gate(ctx, path):
            return {"path": rel_path, "error": err}
        if not path.is_file():
            return {"path": rel_path, "error": "file does not exist"}
    except (OSError, PermissionError) as exc:
        return {"path": rel_path, "error": str(exc)}
    mime = guess_media_type(path)
    if mime is None:
        return {"path": rel_path, "error": "not a viewable image/PDF (png/jpeg/webp/gif/pdf) — "
                                           "read text files with read_file instead"}
    ctx.seen_paths.add(str(path))   # viewed = seen: grounds a later overwrite of this file
    supports = getattr(endpoint, "supports_media", None)
    native = (supports is not None and path.stat().st_size <= NATIVE_MEDIA_MAX_BYTES
              and supports(mime, multimodal=multimodal))
    if native:
        return {"path": rel_path, "media_type": mime, "native": True, "abspath": str(path)}
    return _view_via_vision(rel_path, str(path), prompt, ctx)


def media_from_paths(ctx: RunContext, rels: list[str]) -> list[dict]:
    """`media` entries (path + media_type) for the image/PDF attachments among `rels` that
    the main endpoint can show natively — conversation auto-attach. Unsupported files (wrong
    type, too big, or a text-only endpoint) are skipped: the model can still view_image them,
    which then routes through the vision util.
    """
    try:
        endpoint, ref = ctx.registry.for_model("main", ctx.routine.models)
    except Exception:
        return []
    supports = getattr(endpoint, "supports_media", None)
    if supports is None:
        return []
    out: list[dict] = []
    for rel in rels:
        try:
            path = resolve_rel(ctx.routine.dir, str(rel), ctx.read_roots())
        except (OSError, PermissionError):
            continue
        mime = guess_media_type(path)
        if (mime and path.is_file() and path.stat().st_size <= NATIVE_MEDIA_MAX_BYTES
                and supports(mime, multimodal=ref.multimodal)):
            out.append({"path": str(path), "media_type": mime})
    return out


def do_view_image(action: dict, ctx: RunContext) -> dict:
    """Let the orchestrator SEE an image/PDF: natively when the main MODEL is multimodal
    (the file rides the next message as a `media` block), else via the vision util (text back
    now). Path resolution and gating mirror read_file.
    """
    prompt = str(action.get("prompt") or "")
    try:
        endpoint, ref = ctx.registry.for_model("main", ctx.routine.models)
    except Exception:
        endpoint, ref = None, None
    multimodal = bool(ref.multimodal) if ref else False
    raw = action.get("paths") or ([action["path"]] if action.get("path") else [])
    files = [_view_one(str(p), prompt, endpoint, ctx, multimodal) for p in raw]
    media = [{"path": f.pop("abspath"), "media_type": f["media_type"]}
             for f in files if f.get("native")]
    obs = {"kind": "view_image", "files": files}
    if media:
        obs["media"] = media   # the loop attaches this to the observation's user message
    return obs


def _write_gate(ctx: RunContext, resolved) -> str | None:
    """Backstop for engine-owned and permission-gated writes (grants.deny handles the
    relative-path form; this catches absolute paths into the routine's own dir).
    """
    g = ctx.grants
    if g is None:
        return None
    if resolved.is_relative_to(ctx.routine.dir / "runs"):
        return "runs/ is engine-owned and read-only for the run"
    if resolved.is_relative_to(ctx.routine.dir / OUTPUTS_DIR):
        return (f"{OUTPUTS_DIR}/ is engine-owned and read-only for the run — it is the saved "
                "full text of util output too large for its observation (read_file it); a run "
                "does not rewrite the record of what a util returned")
    # routine.yaml is config — never writable by ANY run (even the improver, even when the
    # recipe is unlocked): config is the user's, changed via the UI or a deferred ask_user.
    # Machine-tunable behavior knobs (deliberation) live in tuning.yaml, which is RECIPE.
    if resolved.name == "routine.yaml":
        return ("routine.yaml is config (permissions, capabilities, budgets, roots) — no run "
                "edits it, not even the routine-improver (machine-tunable knobs live in "
                "tuning.yaml); file a deferred ask_user instead")
    if not g.recipe_unlocked:
        from ..grants import is_recipe_path

        try:
            rel = resolved.relative_to(ctx.routine.dir)
        except ValueError:
            return None
        if is_recipe_path(str(rel)):
            return ("a run never edits its own recipe (main.md / stages/ / "
                    "tuning.yaml) — the routine-improver refines it; file a deferred "
                    "ask_user instead")
    return None


def do_write_file(action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_rel(ctx.routine.dir, action["path"], ctx.write_roots())
        if err := _write_gate(ctx, path):
            return {"kind": "write_file", "path": action["path"], "error": err}
        # Grounding gate: write_file REPLACES a file wholesale. Overwriting one OUTSIDE
        # the routine's own dir (a project file under an fs_write_root) requires having
        # seen it this run — a model that never read the content cannot know what it
        # destroys. The own dir is exempt (state/report rewrites are its normal mode);
        # append adds without destroying; creating a new file needs no grounding.
        if (path.is_file() and not action.get("append")
                and not path.is_relative_to(ctx.routine.dir)
                and str(path) not in ctx.seen_paths):
            return {"kind": "write_file", "path": action["path"],
                    "error": "this OVERWRITES an existing file this run has never read — "
                             "read_file it first (then overwrite knowingly), or use "
                             "edit_file with a verbatim anchor for a targeted change"}
        path.parent.mkdir(parents=True, exist_ok=True)
        data = action["content"]
        if not isinstance(data, str):
            # Structured content arrives as a live JSON value — models need not escape
            # file bodies into strings; we serialize.
            data = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        if action.get("append"):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(data)
        else:
            # Atomic (tmp+rename): another process reading this path — self-audit reading any
            # routine, or the target routine's own run when the improver rewrites its recipe
            # under an fs_write_root — sees the old or new file whole, never a torn write. Its
            # git autocommit / pre-run recipe snapshot can't stage a half-written file either.
            keep = path.stat().st_mode & 0o7777 if path.exists() else None
            atomic_write(path, data, mode=keep)
        ctx.seen_paths.add(str(path))   # written = seen: a rewrite of own output is grounded
        size = path.stat().st_size      # TOTAL bytes on disk after the write
    except (OSError, PermissionError) as exc:
        return {"kind": "write_file", "path": action["path"], "error": str(exc)}
    # `bytes` = payload WRITTEN this action; `size` = the file's total size AFTER it. An
    # append that truly appended shows size == prior + bytes; an overwrite shows size ==
    # bytes — so append-vs-overwrite is provable from the observation alone.
    return {"kind": "write_file", "path": action["path"], "bytes": len(data.encode("utf-8")),
            "append": bool(action.get("append")), "size": size}


def _nearest_anchor_hint(text: str, anchor: str) -> str:
    """When an anchor doesn't match, find the closest ACTUAL line and show it via repr() — so a
    near-miss on an invisible or ambiguous character (a non-ASCII dash like — vs -, a NBSP, a
    tab-vs-spaces, trailing whitespace) is diagnosable at a glance instead of by trial and error.
    read_file renders such characters escaped, so copying the displayed anchor can silently differ
    from the file's real bytes; repr() of the true line shows exactly what to copy. Empty string
    when nothing is close enough (a genuinely absent anchor gets no misleading hint).
    """
    first = anchor.splitlines()[0].strip() if anchor.strip() else ""
    if not first:
        return ""
    lines = text.splitlines()
    match = difflib.get_close_matches(first, [ln.strip() for ln in lines], n=1, cutoff=0.7)
    if not match:
        return ""
    # the raw line whose stripped form matched — show it verbatim via repr()
    actual = next((ln for ln in lines if ln.strip() == match[0]), match[0])
    return (f". Closest line in the file is {actual!r} — note any character that differs from "
            "your anchor (a non-ASCII dash, NBSP, tab vs spaces, or trailing whitespace); "
            "repr() shows the true bytes to copy.")


def do_edit_file(action: dict, ctx: RunContext) -> dict:
    """Anchor-replace in place — revisions cost the diff, not the whole document (the
    write_file counterpart for touching a few lines of a large file).
    """
    try:
        path = resolve_rel(ctx.routine.dir, action["path"], ctx.write_roots())
        if err := _write_gate(ctx, path):
            return {"kind": "edit_file", "path": action["path"], "error": err}
        if not path.is_file():
            return {"kind": "edit_file", "path": action["path"],
                    "error": "file does not exist — create it with write_file"}
        text = path.read_text(encoding="utf-8")
        anchor = str(action["anchor"])
        replacement = str(action.get("replacement") or "")
        count = text.count(anchor)
        if count == 0:
            return {"kind": "edit_file", "path": action["path"],
                    "error": "anchor not found in the file — copy it VERBATIM from a "
                             "read_file observation (whitespace and line breaks included)"
                             + _nearest_anchor_hint(text, anchor)}
        if count > 1 and not action.get("all"):
            return {"kind": "edit_file", "path": action["path"],
                    "error": f"anchor appears {count} times — extend it until it is unique, "
                             "or set all: true to replace every occurrence"}
        new_text = text.replace(anchor, replacement) if action.get("all") \
            else text.replace(anchor, replacement, 1)
        # Atomic + mode-preserving (the file exists — checked above), same reasoning as
        # do_write_file: no torn read/commit for a concurrent reader of this routine's dir.
        atomic_write(path, new_text, mode=path.stat().st_mode & 0o7777)
        ctx.seen_paths.add(str(path))   # an anchored edit is grounded by its verbatim anchor
    except (OSError, PermissionError) as exc:
        return {"kind": "edit_file", "path": action["path"], "error": str(exc)}
    return {"kind": "edit_file", "path": action["path"],
            "replacements": count if action.get("all") else 1,
            "bytes": len(new_text.encode("utf-8"))}


def _memory_topics(mem_dir) -> list[str]:
    if not mem_dir.is_dir():
        return []
    return sorted(p.stem for p in mem_dir.glob("*.md") if p.name != "INDEX.md")


def _memory_index_upsert(mem_dir, name: str, about: str | None) -> None:
    """INDEX.md is engine-owned: one `- <name>.md: <about>` line per note, updated in the
    same operation as the note itself so the catalog can never drift. about=None removes.
    """
    index = mem_dir / "INDEX.md"
    lines = index.read_text(encoding="utf-8").splitlines() if index.exists() else []
    prefix = f"- {name}.md:"
    lines = [ln for ln in lines if not ln.startswith(prefix)]
    if about is not None:
        lines.append(f"{prefix} {about.strip()}")
    atomic_write(index, "\n".join(lines) + ("\n" if lines else ""))


def do_memory_read(action: dict, ctx: RunContext) -> dict:
    name = action["name"]
    mem_dir = ctx.routine.dir / ".memory"
    path = mem_dir / f"{name}.md"
    if not path.is_file():
        return {"kind": "memory_read", "name": name, "missing": True,
                "topics": _memory_topics(mem_dir)}
    content, truncated = truncate(path.read_text(encoding="utf-8", errors="replace"))
    return {"kind": "memory_read", "name": name, "content": content,
            "lines": len(content.splitlines()), "truncated": truncated}


def do_read_rule(action: dict, ctx: RunContext) -> dict:
    """Read a general RULE from the shared library — the only way a run sees rule prose.

    Rules live in ONE place and are read-only to every run: this action never writes, and the
    held set is routine.yaml config the user owns. The prose is deliberately NOT in the
    composed prompt — main.md's Standing practices tail names the held slugs and the run
    fetches the one it needs, so an unread rule costs nothing every turn. `name: "list"`
    returns the catalog (mirroring `util name=list`), which is how a run reaches a rule it
    does not hold: it applies for this run only, and asking the user to make it permanent is
    a finish-summary or deferred-ask_user matter.
    """
    from .. import library_docs

    name = action["name"]
    home = ctx.server.rules_home
    catalog = library_docs.list_docs(home)
    # "held" = bound by this routine's own config, so the model can tell a rule it should
    # ALREADY be applying from one it is consulting for the first time.
    held = set(ctx.routine.rules)
    if name == "list":
        return {"kind": "read_rule", "name": "list",
                "rules": [{"slug": d["slug"], "summary": d["summary"],
                           "held": d["slug"] in held} for d in catalog]}
    raw = library_docs.read_doc(home, name)
    if raw is None:
        return {"kind": "read_rule", "name": name, "missing": True,
                "available": [d["slug"] for d in catalog]}
    body = library_docs.doc_body(raw).strip()
    return {"kind": "read_rule", "name": name, "content": body,
            "lines": len(body.splitlines()), "held": name in held}


def do_memory_write(action: dict, ctx: RunContext) -> dict:
    name = action["name"]
    mem_dir = ctx.routine.dir / ".memory"
    path = mem_dir / f"{name}.md"
    if action.get("delete"):
        existed = path.is_file()
        if existed:
            path.unlink()
            _memory_index_upsert(mem_dir, name, None)
        return {"kind": "memory_write", "name": name, "deleted": True, "existed": existed}
    mem_dir.mkdir(exist_ok=True)
    created = not path.exists()
    data = str(action["content"]).rstrip() + "\n"
    atomic_write(path, data)
    _memory_index_upsert(mem_dir, name, str(action["about"]))
    return {"kind": "memory_write", "name": name, "created": created,
            "lines": len(data.splitlines())}


# Openers a content refusal almost always leads with. Kept conservative and matched only
# against the HEAD of a free-text reply (see _looks_like_refusal) so a genuine answer that
# merely mentions a caveat deep in its body is never misread as a refusal.
