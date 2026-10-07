"""The `decide` action — a typed question put to a DECISION model, probabilities back.

Where `llm` asks a chat model and parses its prose, `decide` asks a decision model
(endpoints/decisions.py: TypeSafe's Jev, OpenAI's Decisions API, the predator server) and gets
the answer as a distribution over the answers it was offered: P(yes) for a yes/no question, a
probability per option for a choice, a probability-weighted level for a score. There is no reply
text, so the empty-completion and refusal-in-prose defences `llmaction.py` carries have nothing
to defend against here — which is the reason the action exists (R2190, decided 2026-10-07).

Several questions over ONE body of evidence go in one call (`questions`): every provider
evaluates them against a single ingestion, and the predator server keeps the encoded images in
its cache across them. The evidence is text or JSON (`evidence`) plus `files` — images go to a
model that takes them, text files are read in as evidence — so a long document is classified
without passing through the run's own context first.
"""

from __future__ import annotations

import json
import re

from ..endpoints.base import IMAGE_MIMES, NATIVE_MEDIA_MAX_BYTES, EndpointError, guess_media_type
from ..endpoints.decisions import (
    ANSWER_TYPES,
    DecisionImage,
    DecisionOption,
    DecisionQuestion,
    decide,
    decision_catalog,
    pick_decision_model,
)
from .run_context import RunContext

QUESTIONS_MAX = 16
OPTIONS_MAX = 26
FILES_MAX = 16
#: The evidence text one call may carry, files included. Jev's state window is 32k tokens;
#: past this a run should decide over an excerpt it chose rather than one a provider truncated.
EVIDENCE_MAX_CHARS = 120_000
QUESTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
#: The name a single-question call's answer is filed under.
SINGLE_NAME = "answer"


def available(server) -> bool:
    """Whether a run on this instance is shown the action at all: only once a decision model
    exists, so an instance that configured none keeps exactly the surface it had.
    """
    return bool(server.decision_models)


def _option(raw: str) -> DecisionOption:
    """`"value: what it means"` → the value and its description; anything else is its own
    value. The value is what the answer reports back, so it is kept short.
    """
    head, sep, tail = raw.partition(":")
    if sep and head.strip() and tail.strip() and len(head.strip()) <= 60:
        return DecisionOption(head.strip(), tail.strip())
    return DecisionOption(raw.strip())


def _question(spec: dict, name: str, where: str) -> tuple[DecisionQuestion | None, list[str]]:
    problems: list[str] = []
    text = spec.get("question")
    if not isinstance(text, str) or not text.strip():
        problems.append(f"{where} needs 'question' — what to decide, self-contained")
    raw = spec.get("options") or []
    if not isinstance(raw, list) or not all(isinstance(o, str) and o.strip() for o in raw):
        return None, [*problems, f"{where}: 'options' must be a list of non-empty strings"]
    kind = spec.get("answer_type") or ("choice" if raw else "yes_no")
    if kind not in ANSWER_TYPES:
        problems.append(f"{where}: 'answer_type' must be one of {list(ANSWER_TYPES)}")
    elif kind == "yes_no" and raw:
        problems.append(f"{where}: a yes_no question takes no 'options' — its answer is P(yes); "
                        "list options only for a choice or a score")
    elif kind != "yes_no" and not 2 <= len(raw) <= OPTIONS_MAX:
        problems.append(f"{where}: a {kind} needs 2-{OPTIONS_MAX} 'options'"
                        + (" (the levels, LOWEST first)" if kind == "score" else ""))
    options = [_option(o) for o in raw]
    if len({o.value for o in options}) != len(options):
        problems.append(f"{where}: two options share a value — the part before ':' must be "
                        "unique, since it is what the answer names")
    if problems:
        return None, problems
    return DecisionQuestion(name, str(kind), str(text).strip(), options), []


def questions_from(action: dict) -> tuple[list[DecisionQuestion], list[str]]:
    """The call's questions — the single flat form or the `questions` batch — and its problems."""
    batch = action.get("questions")
    if not batch:
        one, errs = _question(action, SINGLE_NAME, "kind=decide")
        return ([one] if one else []), errs
    if not isinstance(batch, list) or not 1 <= len(batch) <= QUESTIONS_MAX:
        return [], [f"kind=decide: 'questions' must list 1-{QUESTIONS_MAX} questions"]
    out: list[DecisionQuestion] = []
    problems: list[str] = []
    for i, spec in enumerate(batch):
        where = f"kind=decide questions[{i}]"
        if not isinstance(spec, dict):
            problems.append(f"{where} must be an object")
            continue
        name = str(spec.get("name") or "")
        if not QUESTION_NAME_RE.match(name):
            problems.append(f"{where} needs 'name' — a short snake_case id its answer is filed "
                            f"under, got {name!r}")
            continue
        q, errs = _question(spec, name, where)
        problems += errs
        if q:
            out.append(q)
    if len({q.name for q in out}) != len(out):
        problems.append("kind=decide: question names must be unique")
    return out, problems


def field_problems(obj: dict) -> list[str]:
    """The semantic checks `validate_action` runs for kind=decide (inside the retry cycle)."""
    single = any(obj.get(k) for k in ("question", "options", "answer_type"))
    if single and obj.get("questions"):
        return [("kind=decide takes ONE question (question/options/answer_type) OR several "
                 "('questions', each with its own name/question/options/answer_type) — not "
                 "both")]
    if not single and not obj.get("questions"):
        return [("kind=decide needs 'question' (one) or 'questions' (several over the same "
                 "evidence)")]
    files = obj.get("files")
    problems: list[str] = []
    if files is not None and (not isinstance(files, list) or len(files) > FILES_MAX
                              or not all(isinstance(f, str) and f.strip() for f in files)):
        problems.append(f"kind=decide: 'files' must list at most {FILES_MAX} non-empty paths")
    if obj.get("evidence") in (None, "", [], {}) and not files:
        problems.append("kind=decide needs evidence to decide over: 'evidence' (text or JSON) "
                        "and/or 'files' (images, text files)")
    return problems + questions_from(obj)[1]


def _gather(action: dict, ctx: RunContext
            ) -> tuple[str | dict | list, list[DecisionImage], list[dict], str]:
    """(evidence, images, what each file became, error). Text files fold into the evidence as
    named fields; images ride beside it. Paths resolve and gate exactly as read_file's do.
    """
    # lazily: actions.py imports this module for its checks, and fileops sits above it
    from ..paths import resolve_rel
    from .fileops import _memory_gate, _runs_read_gate, _sniff
    evidence = action.get("evidence") or ""
    texts: dict[str, str] = {}
    images: list[DecisionImage] = []
    seen: list[dict] = []
    for rel in action.get("files") or []:
        try:
            path = resolve_rel(ctx.routine.dir, str(rel), ctx.read_roots())
            if err := _memory_gate(ctx, path) or _runs_read_gate(ctx, path):
                return "", [], [], f"{rel}: {err}"
            if not path.is_file():
                return "", [], [], f"{rel}: not a file"
            mime = guess_media_type(path)
            size, binary = _sniff(path)
            if mime in IMAGE_MIMES:
                if size > NATIVE_MEDIA_MAX_BYTES:
                    return "", [], [], (f"{rel}: {size / 1024 / 1024:.1f} MiB is over the "
                                        f"{NATIVE_MEDIA_MAX_BYTES // 1024 // 1024} MiB an image "
                                        "may be sent at — downscale it first")
                images.append(DecisionImage(path, str(mime)))
                seen.append({"path": str(rel), "as": "image"})
            elif mime is not None or binary:
                return "", [], [], (f"{rel}: a {mime or 'binary file'} is not decision evidence "
                                    "— an image (png/jpeg/webp/gif) or a text file is")
            elif size > EVIDENCE_MAX_CHARS * 4:
                return "", [], [], (f"{rel}: {size:,} bytes is more text than one call carries "
                                    f"({EVIDENCE_MAX_CHARS:,} characters) — decide over the "
                                    "part that matters")
            else:
                texts[str(rel)] = path.read_text(encoding="utf-8", errors="replace")
                seen.append({"path": str(rel), "as": "text"})
        except (OSError, PermissionError) as exc:
            return "", [], [], f"{rel}: {exc}"
        ctx.seen_paths.add(str(path))
    if texts:
        evidence = {**({"evidence": evidence} if evidence else {}),
                    **{f"file {name}": text for name, text in texts.items()}}
    chars = len(evidence if isinstance(evidence, str) else json.dumps(evidence))
    if chars > EVIDENCE_MAX_CHARS:
        return "", [], [], (f"the evidence is {chars:,} characters and one call carries at most "
                            f"{EVIDENCE_MAX_CHARS:,} — decide over the part that matters")
    return evidence, images, seen, ""


def _answer_dict(a) -> dict:
    fields = {"probability": a.probability, "choice": a.choice, "score": a.score,
              "confidence": a.confidence}
    return {"name": a.name, "type": a.type, "probabilities": a.probabilities,
            **{k: v for k, v in fields.items() if v is not None},
            **({"refused": True} if a.refused else {})}


def do_decide(action: dict, ctx: RunContext) -> dict:
    """Put the call's questions to the chosen decision model and return its typed answers."""
    questions, problems = questions_from(action)
    if problems:      # validate_action ran these already; a slash command may not have
        return {"kind": "decide", "error": "; ".join(problems)}
    evidence, images, files, err = _gather(action, ctx)
    if err:
        return {"kind": "decide", "error": err}
    purpose = ("decide · " + str(action.get("say") or questions[0].question))[:80]
    try:
        endpoint, ref = pick_decision_model(ctx.server, action.get("model"), images=bool(images),
                                            roles=ctx.routine.models)
        timeout = ctx.server.decision_endpoints[ref.endpoint].timeout_s
        result = decide(endpoint, ref, evidence, images, questions, timeout=timeout,
                        purpose=purpose)
    except EndpointError as exc:
        return {"kind": "decide", "error": str(exc), **({"files": files} if files else {})}
    ctx.add_usage(result.usage)
    return {"kind": "decide", "model": ref.name, "endpoint": ref.endpoint,
            "served_by": result.model or ref.model, "files": files,
            "answers": [_answer_dict(a) for a in result.answers], "usage": result.usage}


def _line(a: dict) -> str:
    if a.get("refused"):
        return f"- {a['name']}: REFUSED by the model — no answer to this question"
    probs = a.get("probabilities") or {}
    if a["type"] == "yes_no":
        return f"- {a['name']}: P(yes) = {a['probability']:.3f}"
    spread = " · ".join(f"{k} {v:.3f}" for k, v in probs.items())
    conf = f", confidence {a['confidence']:.2f}" if a.get("confidence") is not None else ""
    if a["type"] == "choice":
        return f"- {a['name']}: {a['choice']}{conf} — {spread}"
    levels = " · ".join(f"[{i}] {k} {v:.3f}" for i, (k, v) in enumerate(probs.items()))
    return (f"- {a['name']}: score {a['score']:.2f} on 0..{max(len(probs) - 1, 1)}{conf} — "
            f"{levels}")


def format_decide(obs: dict, kind: str) -> str | None:
    """The observation text for kind=decide (observations.format_observation's chain)."""
    if kind != "decide":
        return None
    if err := obs.get("error"):
        return f"OBSERVATION (decide FAILED): {err}"
    images = sum(1 for f in obs.get("files") or [] if f.get("as") == "image")
    over = f" over {images} image{'s' if images != 1 else ''}" if images else ""
    head = f"OBSERVATION (decide · {obs.get('model')}{over}):"
    return "\n".join([head, *(_line(a) for a in obs.get("answers") or [])])


def catalog_line(server, roles: dict | None = None) -> str:
    """CAPABILITIES' one line naming the decision models a run may pick, or "" when none."""
    rows = [r for r in decision_catalog(server, roles) if "error" not in r]
    if not rows:
        return ""
    parts = [f"{r['name']} ({'text + images' if r['images'] else 'text'}"
             + (f", {r['default']}" if r.get("default") else "") + ")" for r in rows]
    return "Decision models (decide): " + ", ".join(parts)
