"""decompose pipeline: one SCOPED completion per artifact (outline → main → one per stage →
adapted traits), each retried over transport errors and invalid payloads; hard failures fall
back to the verbatim pattern with degraded=True, trait failures degrade softly to verbatim."""

from pathlib import Path

from rsched.config import ModelRef, ServerConfig
from rsched.endpoints.base import Completion
from rsched.workflows.adapt import (
    MAIN_SCHEMA,
    OUTLINE_SCHEMA,
    STAGE_SCHEMA,
    TRAITS_SCHEMA,
    decompose,
)

SEED = Path(__file__).resolve().parents[1] / "library-seed"

OUTLINE = {"stages": [
    {"name": "gather", "scope": "collect the inputs", "inputs": "the task",
     "outputs": "state/data.json"},
    {"name": "not a slug", "scope": "must be dropped", "inputs": "", "outputs": ""},
    {"name": "deliver", "scope": "write the result", "inputs": "state/data.json",
     "outputs": "result.md"},
]}
MAIN = ("entry state machine\n1. **gather** — stages/gather.md\n"
        "2. **deliver** — stages/deliver.md\n")
BODY = "# Stage\n\nread the inputs, do the work, verify it, write the output.\n"
TRAITS = {"traits": [
    {"slug": "web-research",
     "body": "# trait: web research — adapted to this task\nadapted body\n"},
    {"slug": "not-selected", "body": "must be dropped"}]}


class _PipelineEndpoint:
    """Dispatches on the per-call schema — the pipeline's seam. Override payload() to break
    one artifact."""

    def __init__(self):
        self.prompts: list[str] = []
        self.schemas: list[object] = []

    def payload(self, schema, prompt):
        if schema is OUTLINE_SCHEMA:
            return OUTLINE
        if schema is MAIN_SCHEMA:
            return {"main": MAIN}
        if schema is STAGE_SCHEMA:
            return {"body": BODY}
        if schema is TRAITS_SCHEMA:
            return TRAITS
        raise AssertionError(f"unexpected schema: {schema}")

    def complete(self, messages, *, model, schema=None, effort=None, timeout=600, **kw):
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return Completion(text="", parsed=self.payload(schema, prompt))


def _server(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path
    server.libraries_home = SEED
    return server


def _install(monkeypatch, fake):
    import rsched.endpoints as endpoints_mod

    monkeypatch.setattr(endpoints_mod.EndpointRegistry, "for_system",
                        lambda self: (fake, ModelRef(endpoint="x", model="m")))


def test_decompose_pipeline_one_call_per_artifact(monkeypatch, tmp_path):
    """Outline → main → one call per surviving stage → traits; malformed outline names are
    dropped; unknown trait slugs are dropped; nothing degrades."""
    fake = _PipelineEndpoint()
    _install(monkeypatch, fake)
    result = decompose(_server(tmp_path), "general-task", "some task",
                       traits=["web-research"])
    assert fake.schemas == [OUTLINE_SCHEMA, MAIN_SCHEMA, STAGE_SCHEMA, STAGE_SCHEMA,
                            TRAITS_SCHEMA]
    assert result["main"] == MAIN.strip()
    assert result["stages"] == {"gather": BODY.strip(), "deliver": BODY.strip()}
    assert result["traits"] == {"web-research":
                                "# trait: web research — adapted to this task\nadapted body"}
    assert result["degraded"] is False
    # the outline call demands exclusive, covering scopes…
    assert "MUTUALLY EXCLUSIVE" in fake.prompts[0]
    # …the main call sees the outline and must route every stage…
    assert "stages/gather.md" in fake.prompts[1] and "Standing practices" in fake.prompts[1]
    # …each stage call names its own file and carries main + the full outline
    assert "stages/gather.md" in fake.prompts[2] and MAIN.strip() in fake.prompts[2]
    assert "`deliver`" in fake.prompts[3] and "stages/gather.md" in fake.prompts[3]
    # …and the traits call carries the library trait text
    assert "trait: web-research" in fake.prompts[4]


def test_decompose_fallback_returns_no_adapted_traits(tmp_path):
    # no endpoint configured → materialize fallback; the caller copies library traits verbatim
    result = decompose(_server(tmp_path), "general-task", "some task", traits=["ask-policy"])
    assert result["traits"] == {}
    assert result["stages"] == {}
    assert result["main"].strip()
    assert result["degraded"] is True            # …and the caller can SAY it degraded (D41)


class _FlakyEndpoint(_PipelineEndpoint):
    """The first completion of the whole pipeline dies (e.g. truncated/timed out); the
    per-call retry succeeds."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def complete(self, messages, **kw):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("truncated output")
        return super().complete(messages, **kw)


def test_decompose_retries_each_call_before_degrading(monkeypatch, tmp_path):
    """D41: one flaky completion must not ship a stageless routine — the CALL retries,
    the pipeline survives."""
    fake = _FlakyEndpoint()
    _install(monkeypatch, fake)
    result = decompose(_server(tmp_path), "general-task", "some task")
    # 1 failed outline + retried outline + main + two stages (no traits selected)
    assert fake.calls == 5 and fake.schemas[0] is OUTLINE_SCHEMA
    assert result["stages"] == {"gather": BODY.strip(), "deliver": BODY.strip()}
    assert result["degraded"] is False


class _StubStageEndpoint(_PipelineEndpoint):
    def payload(self, schema, prompt):
        if schema is STAGE_SCHEMA:
            return {"body": "Do the gather step."}          # the observed one-line stub
        return super().payload(schema, prompt)


def test_decompose_stub_stage_forces_fallback(monkeypatch, tmp_path):
    """A one-line stage module is worse than no decomposition — the stub guard degrades the
    pipeline to the verbatim pattern instead of shipping it."""
    fake = _StubStageEndpoint()
    _install(monkeypatch, fake)
    result = decompose(_server(tmp_path), "general-task", "some task")
    assert result["stages"] == {}
    assert result["degraded"] is True


class _UnroutedMainEndpoint(_PipelineEndpoint):
    def payload(self, schema, prompt):
        if schema is MAIN_SCHEMA:
            return {"main": "entry that routes only stages/gather.md"}
        return super().payload(schema, prompt)


def test_decompose_main_must_route_every_stage(monkeypatch, tmp_path):
    """main.md is the progress diagram — a main that drops an outlined stage is rejected
    (retried, then degraded), never shipped half-routed."""
    fake = _UnroutedMainEndpoint()
    _install(monkeypatch, fake)
    result = decompose(_server(tmp_path), "general-task", "some task")
    assert result["degraded"] is True
    assert result["stages"] == {}


class _BrokenTraitsEndpoint(_PipelineEndpoint):
    def payload(self, schema, prompt):
        if schema is TRAITS_SCHEMA:
            raise RuntimeError("traits call died")
        return super().payload(schema, prompt)


def test_decompose_trait_failure_degrades_softly(monkeypatch, tmp_path):
    """A dead traits call must NOT throw away a good main+stages — the caller copies the
    library traits verbatim instead."""
    fake = _BrokenTraitsEndpoint()
    _install(monkeypatch, fake)
    result = decompose(_server(tmp_path), "general-task", "some task",
                       traits=["web-research"])
    assert result["degraded"] is False
    assert result["stages"] == {"gather": BODY.strip(), "deliver": BODY.strip()}
    assert result["traits"] == {}


def test_decompose_binds_params_inline(monkeypatch, tmp_path):
    """D41: resolved parameter VALUES are compiled into the generated files — every
    generation call carries each value plus the inline-binding order and the anti-stub rule."""
    fake = _PipelineEndpoint()
    _install(monkeypatch, fake)
    decompose(_server(tmp_path), "general-task", "some task",
              params={"SITE_URL": "https://x.example"})
    for p in fake.prompts[:4]:                    # outline, main, and both stage calls
        assert "- SITE_URL: https://x.example" in p
        assert "Bind each resolved VALUE inline" in p


# ---- pinned deliverables (META["pin"]) ----------------------------------------------------------


class _PinEndpoint(_PipelineEndpoint):
    """Returns a decomposition that keeps or drops the pattern's pinned deliverable."""

    def __init__(self, keep_pin: bool):
        super().__init__()
        self.keep_pin = keep_pin

    def payload(self, schema, prompt):
        if schema is OUTLINE_SCHEMA:
            return {"stages": [{"name": "interrogate", "scope": "interrogate the draft",
                                "inputs": "the draft", "outputs": "the refined result"}]}
        if schema is MAIN_SCHEMA:
            main = ("interrogate the draft (stages/interrogate.md), then write "
                    "state/wizard_result.json" if self.keep_pin
                    else "# Scheduler research\n\nfollow stages/interrogate.md and post "
                         "findings to decisions")
            return {"main": main}
        return super().payload(schema, prompt)


def test_decompose_enforces_pinned_deliverables(monkeypatch, tmp_path):
    """clarify-instruction pins state/wizard_result.json. A decomposition that drops it — the
    generator built the DRAFTED routine instead of the clarify flow (observed 2026-07-16, the
    run then dead-ended with 'ended without a result') — falls back to the verbatim pattern,
    which always keeps the deliverable."""
    fake = _PinEndpoint(keep_pin=False)
    _install(monkeypatch, fake)
    result = decompose(_server(tmp_path), "clarify-instruction",
                       "draft: research scheduler improvements each run")
    assert "PINNED DELIVERABLES" in fake.prompts[0]          # the prompt demands the pin…
    assert "state/wizard_result.json" in fake.prompts[0]
    assert result["stages"] == {}                            # …and the drop forced the fallback
    assert result["degraded"] is True
    assert "state/wizard_result.json" in result["main"]


def test_decompose_accepts_a_result_that_keeps_the_pin(monkeypatch, tmp_path):
    fake = _PinEndpoint(keep_pin=True)
    _install(monkeypatch, fake)
    result = decompose(_server(tmp_path), "clarify-instruction", "some draft")
    assert result["degraded"] is False
    assert "state/wizard_result.json" in result["main"]
    assert result["stages"] == {"interrogate": BODY.strip()}


def test_decompose_without_pins_never_falls_back_over_them(monkeypatch, tmp_path):
    """general-task declares no pin — an arbitrary (well-routed) main stays accepted."""
    fake = _PinEndpoint(keep_pin=False)
    _install(monkeypatch, fake)
    result = decompose(_server(tmp_path), "general-task", "some task")
    assert "PINNED" not in fake.prompts[0]
    assert result["degraded"] is False
    assert result["main"].startswith("# Scheduler research")
