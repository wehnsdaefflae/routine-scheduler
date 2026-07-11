"""decompose + strip_inactive_improve: inactive improve-* passes never reach a routine's
files — the generator is told the active set, and its output is stripped deterministically."""

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
            "modules": [{"name": "improve", "body": DOC}]})


def test_decompose_states_active_set_and_strips_leaks(monkeypatch, tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path
    server.libraries_home = SEED
    fake = _FakeEndpoint()
    import rsched.endpoints as endpoints_mod

    monkeypatch.setattr(endpoints_mod.EndpointRegistry, "for_system",
                        lambda self: (fake, ModelRef(endpoint="x", model="m")))
    result = decompose(server, "general-task", "some task", fragments=["improve-bugfix"])
    # the prompt names the active set, so the generator can omit inactive lenses…
    assert "ACTIVE FRAGMENTS" in fake.prompts[0] and "improve-bugfix" in fake.prompts[0]
    # …and whatever it renders anyway is stripped
    assert "improve-ui" not in result["main"]
    assert "improve-ui" not in result["modules"]["improve"]
    assert "improve-bugfix" in result["modules"]["improve"]
