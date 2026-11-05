"""A CONVERSATION's `machines:` binding reaches its COMPOSED PROMPT (D216 part b).

The parity pin this file exists for: a conversation binds machines exactly like a routine
(D102, `web/api_conversation_config.py` PATCH `machines`), and the engine injects
RSCHED_MACHINES for it (`engine/exec_env._machine_env`) — but until this file nothing
asserted that the bound box is NAMED in the prompt the conversation's model reads. The
surfaces that touch it were each pinned on their own side: `tests/test_conversations.py`
`test_conversation_machines_bind_and_validate` stops at routine.yaml, and
`tests/test_composer.py`'s machine tests build a ROUTINE context. A conversation told
nothing about its GPU box spends its reply discovering hardware it was configured with.

Asserted on the COMPOSED TEXT (`build_system_prompt`), not on `ctx.routine.machines`, and
driven from the YAML on disk through the real `load_routine`, so the whole chain is covered:
routine.yaml → config loader → `capabilities._machine_notes` → the CAPABILITIES section.
"""
from types import SimpleNamespace

import yaml

from helpers import run_context, server_config
from rsched.engine.composer import build_system_prompt

GPU = SimpleNamespace(name="gpu-box", host="10.0.0.9", user="rs", port=22, host_key="",
                      workdir="", share="", description="the RTX 4090 box", tags=["gpu"],
                      key_var="GPU_KEY")


def _conversation(make_routine, tmp_path, machines: list[str]):
    """A conversation-shaped run context: a routine dir whose yaml carries `machines:`, with
    the server's conversations_home set to its parent — HOME is what makes a run a
    conversation (`engine/runkind.is_conversation`).
    """
    d = make_routine(slug="convo-mach")
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["machines"] = machines
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    ctx = run_context(d, "20260708-070000",
                      server=server_config(libraries_home=tmp_path / "libraries",
                                           conversations_home=d.parent))
    ctx.server.machines = {"gpu-box": GPU}
    return ctx


def test_conversation_prompt_names_its_bound_machines(make_routine, tmp_path):
    ctx = _conversation(make_routine, tmp_path, ["gpu-box"])
    assert ctx.routine.machines == ["gpu-box"]          # the loader kept the binding
    sp = build_system_prompt(ctx, "## Run flow", "train the model", "digest", [])
    assert "# INSTRUCTION (your assigned task)" in sp   # it really is a conversation
    assert "Remote machines this routine is bound to" in sp
    assert "gpu-box — the RTX 4090 box [gpu]" in sp


def test_conversation_without_machines_gets_no_machine_section(make_routine, tmp_path):
    """The row is absent, not empty: an unbound conversation must not read as having hardware."""
    ctx = _conversation(make_routine, tmp_path, [])
    sp = build_system_prompt(ctx, "## Run flow", "write the digest", "digest", [])
    assert "Remote machines this routine is bound to" not in sp
    # the ROW, not the bare name: the action schema's `request` gloss carries the literal
    # example "machine:gpu-box", so a name-only assertion can never hold in a composed prompt
    assert "the RTX 4090 box" not in sp
