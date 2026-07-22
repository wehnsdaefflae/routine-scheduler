"""Dispatch a validated action to its effect and return the observation dict.

Handles util / read_file / write_file / edit_file / memory_read / memory_write / llm here.
Control-flow kinds (spawn, subruns, kill, wait, finish) live in loop.py — they change the
run's state machine — and the user-facing kinds (ask_user, write_util) in interact.py.
Every observation dict feeds both the transcript event and (via
composer.format_observation) the next user message.
"""

from __future__ import annotations

import json

from .. import sandbox, utils_lib
from ..endpoints.base import NATIVE_MEDIA_MAX_BYTES, EndpointError, guess_media_type
from ..ids import is_slug
from ..oauth import store as oauth_store
from ..paths import atomic_write, resolve_rel
from ..statemap import STAGES_DIR
from .observations import truncate
from .run_context import RunContext

READ_DEFAULT_MAX_LINES = 200
UTIL_DEFAULT_TIMEOUT_S = 300
# argparse exits 2 on bad arguments — the deterministic "called with wrong syntax" signal
# for per-util telemetry (a util not using argparse may exit 1 for everything; then its
# usage errors count as plain errors, which is the honest fallback).
USAGE_ERROR_EXIT = 2
VISION_UTIL = "vision"
VIEW_DEFAULT_PROMPT = ("Describe this file in full detail — transcribe any text verbatim and "
                       "note structure, data, and notable visual elements.")


def _connection_env(ctx: RunContext) -> dict[str, str]:
    """The routine's bound OAuth connections resolved to {<PROVIDER>_ACCESS_TOKEN: token}, passed
    to run_util as extra_secrets. A util only sees a token it declares AND the routine binds; a
    missing / needs-reauth binding is simply absent (the util then fails for want of a token).
    """
    if not ctx.routine.connections:
        return {}
    env, _warnings = oauth_store.tokens_for_routine(ctx.routine.connections)
    return env


def do_util(action: dict, ctx: RunContext) -> dict:
    name = action["name"]
    args = [str(a) for a in (action.get("args") or [])]
    home = ctx.server.utils_home
    if name == "list":  # discovery: `gu list` — the catalog is derived live, never stale
        # With a util name in args, list ONLY that util's entry (usage + tags + secrets):
        # the full catalog is already in CAPABILITIES, so re-listing everything to learn
        # one util's flags re-buys ~3k tokens of known information.
        target = str(args[0]).lstrip("-") if args else ""
        if target and target != "all":
            entry = next((u for u in utils_lib.list_utils(home) if u["name"] == target), None)
            if entry is None:
                return {"kind": "util", "name": "list", "target": target, "missing": True,
                        "available": [u["name"] for u in utils_lib.list_utils(home)]}
            lines = [f"- {entry['name']} — {entry['summary']}"]
            if entry.get("usage"):
                lines.append(f"    {entry['usage']}")
            if entry.get("tags"):
                lines.append(f"    tags: {', '.join(entry['tags'])}")
            if entry.get("secrets"):
                lines.append(f"    secrets: {', '.join(entry['secrets'])}")
            return {"kind": "util", "name": "list", "target": target,
                    "listing": "\n".join(lines)}
        return {"kind": "util", "name": "list", "listing": utils_lib.catalog_text(home)}
    if name == "show":  # read a util's SOURCE — write_util's counterpart (repair needs read)
        target = str(args[0]) if args else ""
        source = utils_lib.read_util(home, target) if target and is_slug(target) else None
        if source is None:
            return {"kind": "util", "name": "show", "target": target, "missing": True,
                    "available": [u["name"] for u in utils_lib.list_utils(home)]}
        content, truncated = truncate(source, cap=24_000)
        return {"kind": "util", "name": "show", "target": target, "source": content,
                "truncated": truncated}
    if not utils_lib.exists(home, name):
        ctx.count_util(name, "missing")
        return {"kind": "util", "name": name, "missing": True,
                "available": [u["name"] for u in utils_lib.list_utils(home)]}
    code, out, err = utils_lib.run_util(
        home, name, args, timeout=int(action.get("timeout_s") or UTIL_DEFAULT_TIMEOUT_S),
        policy=sandbox.policy_for_run(ctx.server, ctx.routine),
        extra_secrets=_connection_env(ctx))
    # Per-util reliability telemetry (util_stats → the Stats tab).
    ctx.count_util(name, "ok" if code == 0
                   else ("usage_error" if code == USAGE_ERROR_EXIT else "error"))
    stdout, trunc_out = truncate(out)
    # On failure, stderr is the repair material — keep the whole trace where possible
    # (truncate preserves head+tail, so the exception at the traceback's end survives).
    stderr, trunc_err = truncate(err, cap=8000 if code != 0 else 2000)
    obs = {"kind": "util", "name": name, "args": args, "exit": code,
           "stdout": stdout, "stderr": stderr, "truncated": trunc_out or trunc_err}
    if code != 0:
        # A failed call teaches the correct one — and the repair path. Without this nudge
        # the model's rational move is a silent workaround, and the next routine hits the
        # same wall (seen live: page-fetch broken, run fell back to websearch, nobody told).
        entry = next((u for u in utils_lib.list_utils(home) if u["name"] == name), None)
        if entry and entry.get("usage"):
            obs["usage"] = entry["usage"]
        # The repair route depends on the routine's grants: with util authoring, fix it in
        # place; without, escalate — never let it silently work around a broken util.
        if ctx.grants is None or ctx.grants.allows_kind("write_util"):
            repair = (f'If the inputs were right, the util itself may be broken — read it with '
                      f'{{"kind": "util", "name": "show", "args": ["{name}"]}}, fix it, and '
                      f'write_util the corrected script (selftest-gated; the fix benefits every '
                      f'routine). If the environment lacks something no script can install '
                      f'(system packages, hardware), file a deferred ask_user so the operator '
                      f'sees it.')
        else:
            repair = (f'If the inputs were right, the util itself may be broken — read it with '
                      f'{{"kind": "util", "name": "show", "args": ["{name}"]}} to confirm, then '
                      f'file a deferred ask_user naming the util, the failing call, and the '
                      f'error (this routine holds no util-authoring permission, so it cannot '
                      f'revise utils itself). Never silently work around a broken util.')
        obs["hint"] = (
            f'call shape: every argument goes in `args` as a JSON array of strings, e.g. '
            f'{{"say": "…", "kind": "util", "name": "{name}", "args": ["<argument>", "--json"]}}. '
            + repair)
    return obs


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
        return ("reading previous runs is not among this routine's permissions "
                "(the run-history permission unlocks it; depth last/all is a capability)")
    if g.run_history == "last":
        prior = sorted(d.name for d in runs_dir.iterdir()
                       if d.is_dir() and d.name != ctx.root_run_dir.name)
        last = prior[-1] if prior else None
        if not rel.parts or rel.parts[0] != last:
            return (f"this routine's run-history permission covers only the LAST previous "
                    f"run ({'runs/' + last if last else 'none exists yet'}); "
                    f"raising its run-history depth to 'all' would cover all of them")
    return None


def _read_one(rel_path: str, action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_rel(ctx.routine.dir, rel_path, ctx.routine.fs_read_roots)
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
    content, truncated = truncate("\n".join(window))
    return {"path": rel_path, "start_line": start,
            "end_line": min(start - 1 + max_lines, len(lines)), "total_lines": len(lines),
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
    home = ctx.server.utils_home
    if not utils_lib.exists(home, VISION_UTIL):
        return "error: the `vision` util is not installed, so this file cannot be described"
    args = [abspath, "--prompt", prompt or VIEW_DEFAULT_PROMPT, "--json"]
    code, out, err = utils_lib.run_util(home, VISION_UTIL, args, timeout=UTIL_DEFAULT_TIMEOUT_S,
                                        policy=sandbox.policy_for_run(ctx.server, ctx.routine))
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
        path = resolve_rel(ctx.routine.dir, rel_path, ctx.routine.fs_read_roots)
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
            path = resolve_rel(ctx.routine.dir, str(rel), ctx.routine.fs_read_roots)
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
    # routine.yaml is config — never writable by ANY run (even the improver, even when the
    # recipe is unlocked): config is the user's, changed via the UI or a deferred ask_user.
    # Machine-tunable behavior knobs (deliberation) live in tuning.yaml, which is RECIPE.
    if resolved.name == "routine.yaml":
        return ("routine.yaml is config (permissions, capabilities, budgets, roots) — no run "
                "edits it, not even the routine-improver (machine-tunable knobs live in "
                "tuning.yaml); file a deferred ask_user instead")
    if not getattr(g, "recipe_unlocked", False):
        from ..grants import RECIPE_PREFIXES

        try:
            rel = resolved.relative_to(ctx.routine.dir)
        except ValueError:
            return None
        rel_s = str(rel)
        if any(rel_s == p.rstrip("/") or rel_s.startswith(p) for p in RECIPE_PREFIXES):
            return ("a run never edits its own recipe (main.md / stages/ / traits/ / "
                    "tuning.yaml) — the routine-improver refines it; file a deferred "
                    "ask_user instead")
    return None


def do_write_file(action: dict, ctx: RunContext) -> dict:
    try:
        roots = ctx.routine.fs_write_roots
        path = resolve_rel(ctx.routine.dir, action["path"], roots)
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


def do_edit_file(action: dict, ctx: RunContext) -> dict:
    """Anchor-replace in place — revisions cost the diff, not the whole document (the
    write_file counterpart for touching a few lines of a large file).
    """
    try:
        path = resolve_rel(ctx.routine.dir, action["path"], ctx.routine.fs_write_roots)
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
                             "read_file observation (whitespace and line breaks included)"}
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


def do_read_trait(action: dict, ctx: RunContext) -> dict:
    """CONSULT a practice module from the shared library — read-only, for THIS run only.

    Nothing is written: the recipe invariant holds (a run never adds to its own traits/), and
    the prose reaches the model as an ordinary observation rather than a permanent standing
    practice. Making a trait permanent stays the user's call, from the routine page or the
    conversation header. `name: "list"` returns the catalog, mirroring `util name=list` — the
    trait catalog is deliberately NOT in the composed prompt, so discovery costs one turn
    rather than every turn's cache.
    """
    from .. import library_docs

    name = action["name"]
    try:
        home = ctx.server.traits_home
    except AttributeError:      # bare test contexts carry no server config
        return {"kind": "read_trait", "name": name, "missing": True, "available": []}
    catalog = library_docs.list_docs(home)
    # "held" = already one of this routine's own standing practices, so the model can tell a
    # module it should ALREADY be following from one it is consulting for the first time.
    held = {p.stem for p in ctx.routine.dir.joinpath("traits").glob("*.md")}
    if name == "list":
        return {"kind": "read_trait", "name": "list",
                "traits": [{"slug": d["slug"], "summary": d["summary"],
                            "held": d["slug"] in held} for d in catalog]}
    raw = library_docs.read_doc(home, name)
    if raw is None:
        return {"kind": "read_trait", "name": name, "missing": True,
                "available": [d["slug"] for d in catalog]}
    body = library_docs.doc_body(raw).strip()
    return {"kind": "read_trait", "name": name, "content": body,
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
_REFUSAL_MARKERS = (
    "i can't help with that", "i cannot help with that",
    "i can't assist with that", "i cannot assist with that",
    "i can't help you with that", "i cannot help you with that",
    "i'm unable to help with that", "i am unable to help with that",
    "i'm not able to help with that", "i am not able to help with that",
    "i can't provide", "i cannot provide",
    "i can't comply with", "i cannot comply with",
    "i can't fulfill", "i cannot fulfill", "i can't fulfil", "i cannot fulfil",
    "i can't create", "i cannot create",
    "i'm sorry, but i can't", "i'm sorry, but i cannot",
    "i'm sorry, i can't", "i'm sorry, i cannot",
    "i won't be able to help with that", "i must decline",
    "it goes against my guidelines", "against my programming",
)


def _looks_like_refusal(text: str) -> bool:
    """Heuristic: does a free-text tool-call reply read as a content refusal rather than an
    answer? Conservative on purpose — only a marker in the reply's HEAD (first ~200 chars)
    counts, since real refusals open with the decline; this trades recall for precision so we
    don't reroute genuine answers to the uncensored model.
    """
    head = (text or "").strip().lower()[:200]
    return bool(head) and any(m in head for m in _REFUSAL_MARKERS)


def do_llm(action: dict, ctx: RunContext) -> dict:
    messages = []
    if action.get("system"):
        messages.append({"role": "system", "content": action["system"]})
    messages.append({"role": "user", "content": action["prompt"]})
    schema = action.get("response_schema")
    purpose = ("llm · " + str(action.get("say") or "sub-call"))[:80]
    try:
        endpoint, ref = ctx.registry.for_model("tool_call", ctx.routine.models)
        completion = endpoint.complete(messages, model=ref.model, schema=schema,
                                       effort=ref.effort, temperature=ref.temperature,
                                       max_tokens=ref.max_tokens, purpose=purpose,
                                       kind="llm_action")
    except EndpointError as exc:
        return {"kind": "llm", "error": str(exc)}
    ctx.add_usage(completion.usage)

    # Refusal referral (opt-in): the tool-call model declined a free-text request and the
    # routine configured an `uncensored` model — re-issue the SAME prompt to it. Only
    # free-text replies (parsed is None) are considered; a schema'd/structured reply is an
    # answer, not a refusal. Referral is silent for routines that leave the role unset.
    endpoint_name, model_name, referred = ref.endpoint, ref.model, False
    if completion.parsed is None and _looks_like_refusal(completion.text):
        target = ctx.registry.for_uncensored(ctx.routine.models)
        if target is not None:
            u_endpoint, u_ref = target
            try:
                u_completion = u_endpoint.complete(messages, model=u_ref.model, schema=schema,
                                                   effort=u_ref.effort,
                                                   temperature=u_ref.temperature,
                                                   max_tokens=u_ref.max_tokens,
                                                   purpose=(purpose + " · referred")[:80],
                                                   kind="llm_action")
            except EndpointError:
                u_completion = None
            if u_completion is not None:
                ctx.add_usage(u_completion.usage)
                completion, referred = u_completion, True
                ctx.referrals += 1
                endpoint_name, model_name = u_ref.endpoint, u_ref.model

    reply = completion.text
    if completion.parsed is not None:
        reply = json.dumps(completion.parsed, ensure_ascii=False, indent=1)
    reply, truncated = truncate(reply)
    out = {"kind": "llm", "endpoint": endpoint_name, "model": model_name,
           "reply": reply, "usage": completion.usage, "truncated": truncated}
    if referred:
        out["referred"] = True
    return out


DISPATCH = {
    "util": do_util,
    "read_file": do_read_file,
    "view_image": do_view_image,
    "write_file": do_write_file,
    "edit_file": do_edit_file,
    "memory_read": do_memory_read,
    "memory_write": do_memory_write,
    "read_trait": do_read_trait,
    "llm": do_llm,
}


def dispatch(action: dict, ctx: RunContext) -> dict:
    return DISPATCH[action["kind"]](action, ctx)
