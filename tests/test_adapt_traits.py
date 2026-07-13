"""decompose + strip_inactive_improve: selected traits come back ADAPTED (or copied
verbatim on fallback), and improve-* sections a legacy pattern may still carry are
stripped deterministically unless selected — the improve-* traits themselves are retired
from the library (the routine-improver meta routine owns those lenses now)."""

from pathlib import Path

from rsched.config import ModelRef, ServerConfig
from rsched.endpoints.base import Completion
from rsched.workflows.adapt import decompose, strip_inactive_improve

SEED = Path(__file__).resolve().parents[1] / "library-seed"

DOC = """# Improve

intro prose

### improve-bugfix — anything broken?
fix it.
more lines.

### improve-ui — output tidy?
polish it.

## Wrap up
done.
"""


def test_strip_drops_only_inactive_sections():
    out = strip_inactive_improve(DOC, ["improve-bugfix"])
    assert "improve-bugfix" in out and "fix it." in out
    assert "improve-ui" not in out and "polish it." not in out
    assert "intro prose" in out and "## Wrap up" in out and "done." in out


def test_strip_keeps_everything_when_all_active():
    assert strip_inactive_improve(DOC, ["improve-bugfix", "improve-ui"]) == DOC


class _FakeEndpoint:
    def __init__(self):
        self.prompts = []

    def complete(self, messages, *, model, schema=None, effort=None, timeout=600, **kw):
        self.prompts.append(messages[0]["content"])
        return Completion(text="", parsed={
            "main": "entry state machine\n\n### improve-ui — leaked\nshould be stripped\n",
            "modules": [{"name": "improve", "body": DOC}],
            "traits": [{"slug": "web-research",
                        "body": "# trait: web research — adapted to this task\nadapted body\n"},
                       {"slug": "not-selected", "body": "must be dropped"}]})


def test_decompose_adapts_traits_and_strips_leaks(monkeypatch, tmp_path):
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
    # …and any improve-* section a legacy pattern leaked is stripped (none selected here)
    assert "improve-ui" not in result["main"]
    assert "improve-ui" not in result["modules"]["improve"]
    assert "improve-bugfix" not in result["modules"]["improve"]


def test_decompose_fallback_returns_no_adapted_traits(tmp_path):
    # no endpoint configured → materialize fallback; the caller copies library traits verbatim
    server = ServerConfig()
    server.routines_home = tmp_path
    server.libraries_home = SEED
    result = decompose(server, "general-task", "some task", traits=["ask-policy"])
    assert result["traits"] == {}
    assert result["modules"] == {}
    assert result["main"].strip()
