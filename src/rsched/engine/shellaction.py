"""The `shell` ACTION KIND: run ONE ad-hoc command through `bash -c` inside the run's jail.

Its own module because `executor.py` sits at its size ceiling and this kind owns a
responsibility the others do not: it is the one handler whose ARGUMENT is a command line, which
is why the credential screen on the way into the record (`commandscreen.py`) lives at this seam.
"""

from __future__ import annotations

from .. import sandbox, shellrun
from ..paths import expand
from . import commandscreen
from .control import cancelled_for_turn
from .jailnotes import jail_notes as _jail_notes
from .output_compression import command_output
from .run_context import RunContext, work_dir
from .shellwrite import marks_a_write, record_ungated_write


def do_shell(action: dict, ctx: RunContext) -> dict:
    """Run ONE ad-hoc command through `bash -c` inside the run's Landlock jail — the escape
    hatch, gated by the `shell` capability. No call-time secret gate: the command is handed NO
    store secret. The jail and empty secret env are `shellrun`'s; capture, timeout and abort
    are the shared runner's (`utils_run.run_jailed`); truncation and the `.util_outputs/`
    spill are the same a util call gets.

    No advisory tail, unlike `util`'s: a non-zero exit here is usually the ANSWER (`grep -q`,
    `test -f`, a failing suite), not a mistake to correct.

    **A shell write is an UNGATED write, deliberately, and the observation says so** (D158).
    Every write protection lives in the FILE-action gate (`fileops._write_gate`); a `bash -c`
    redirect calls none of it — inside the routine's OWN dir the kernel refuses those writes
    regardless (D179, `ownseal`), so what stays ungated is every path OUTSIDE it. Which writes
    a shell line performs is undecidable, so nothing refuses one; instead `ungated_write` marks
    any write-shaped command ("may have written", never "did not"), and the first one per run
    files an `ungated_write` health event (`engine/shellwrite.py`).

    **What the RECORD stores is screened, what runs is not** (D317). The command EXECUTES as
    the run wrote it; the copy that reaches the observation — and from there the transcript, the
    health row and full-text search — has every credential value this instance stores replaced
    by `[secret NAME redacted]`, the same vocabulary the output seam uses. A credential the
    instance does NOT hold is matched by nothing and stays verbatim: see `commandscreen.py`.
    """
    command = str(action.get("command") or "")
    cwd = work_dir(ctx)
    if raw := str(action.get("path") or "").strip():
        candidate = expand(raw)
        cwd = candidate if candidate.is_absolute() else (work_dir(ctx) / candidate)
    policy = sandbox.policy_for_ctx(ctx)   # the jail AND the dead-root note (R2260)
    result = shellrun.run_shell(
        command, policy=policy, libraries_home=ctx.server.libraries_home, cwd=cwd,
        timeout=int(action.get("timeout_s") or shellrun.SHELL_DEFAULT_TIMEOUT_S),
        aborted=ctx.aborted, cancelled=cancelled_for_turn(ctx))
    out, err, code = result["stdout"], result["stderr"], int(result["exit"])
    recorded = commandscreen.screened(command)
    # Deferred: `executor` imports this module for its DISPATCH row, so a top-level import
    # back into it would be a cycle. The killed-child note is shared with util and script.
    from .executor import _note_if_killed
    killed = _note_if_killed(ctx, "shell", recorded[:60], code)
    obs = {"kind": "shell", "command": recorded, "exit": code,
           **command_output(ctx, "shell", out, err, code),
           **killed, **_jail_notes(policy, out, err, code)}
    obs["truncated"] = obs["truncated"] or result["truncated"]
    if marks_a_write(command):
        # D158: no gate saw this write, so the record (and one health event) is all there is.
        obs["ungated_write"] = True
        record_ungated_write(ctx, recorded)
    if str(cwd) != str(work_dir(ctx)):
        obs["cwd"] = str(cwd)
    if result["timed_out"]:
        obs["timed_out"] = True
    if result["aborted"]:
        obs["aborted"] = True
    if result.get("cancelled"):
        obs["cancelled"] = True
    return obs
