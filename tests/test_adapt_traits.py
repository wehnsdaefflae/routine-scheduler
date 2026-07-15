"""decompose: selected traits come back ADAPTED (keyed by slug, unknown slugs dropped),
or copied verbatim by the caller on the no-endpoint materialize fallback."""

from pathlib import Path

from rsched.config import ModelRef, ServerConfig
from rsched.endpoints.base import Completion
from rsched.workflows.adapt import decompose

SEED = Path(__file__).resolve().parents[1] / "library-seed"


class _FakeEndpoint:
    def __init__(self):
        self.prompts = []

    def complete(self, messages, *, model, schema=None, effort=None, timeout=600, **kw):
        self.prompts.append(messages[0]["content"])
        return Completion(text="", parsed={
            "main": "entry state machine\n",
            "stages": [{"name": "gather", "body": "# Gather\n\ndo the work.\n"},
                       {"name": "not a slug", "body": "dropped"},
                       {"name": "empty-body", "body": "   "}],
            "traits": [{"slug": "web-research",
                        "body": "# trait: web research — adapted to this task\nadapted body\n"},
                       {"slug": "not-selected", "body": "must be dropped"}]})


def test_decompose_adapts_traits(monkeypatch, tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path
    server.libraries_home = SEED
    fake = _FakeEndpoint()
    import rsched.endpoints as endpoints_mod

    monkeypatch.setattr(endpoints_mod.EndpointRegistry, "for_system",
                        lambda self: (fake, ModelRef(endpoint="x", model="m")))
    result = decompose(server, "general-task", "some task", traits=["web-research"])
    # the prompt carries the selected traits' library text and asks for adaptation…
    assert "TRAITS" in fake.prompts[0] and "trait: web-research" in fake.prompts[0]
    assert "Standing practices" in fake.prompts[0]
    # …the adapted copy comes back keyed by slug, unknown slugs are dropped…
    assert result["traits"] == {"web-research":
                                "# trait: web research — adapted to this task\nadapted body"}
    # …and only well-formed stages survive (kebab-case name, non-blank body)
    assert result["main"] == "entry state machine"
    assert result["stages"] == {"gather": "# Gather\n\ndo the work.\n"}


def test_decompose_fallback_returns_no_adapted_traits(tmp_path):
    # no endpoint configured → materialize fallback; the caller copies library traits verbatim
    server = ServerConfig()
    server.routines_home = tmp_path
    server.libraries_home = SEED
    result = decompose(server, "general-task", "some task", traits=["ask-policy"])
    assert result["traits"] == {}
    assert result["stages"] == {}
    assert result["main"].strip()
