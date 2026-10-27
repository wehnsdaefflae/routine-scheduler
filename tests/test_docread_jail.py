"""Where read_file's document conversion is WRITTEN — the one path the util's own jail can take.

`engine/docread.py` asks the `doc-read` util to convert a PDF or an Office file inside the
RUN's jail (`sandbox.policy_for_ctx`), and that jail narrows the routine's own directory to its
unsealed children (`ownseal`, D179): `.doc_cache/` — the engine's conversion cache — is one of
`ownseal.SEALED_DIRS`, so the util has no right to create a name inside it. Landlock has no deny
rule, so this is not something the engine can hand over by creating the file first: `--out`'s
PARENT must be a directory the jail mounts read-write, because the util atomic-writes a temp file
BESIDE its target before renaming (`util-seed/utils/doc-read/main.py:write_atomic`).

It was silent for as long as it was, because `docread.converted` returns a CACHE HIT before it
ever reaches the util — only a document never converted in that routine before bites. So the
tests here convert a FRESH document, and the destination assertion is made against
`ownseal.SEALED_DIRS` itself, so adding a sealed directory (or re-sealing an unsealed one)
cannot quietly reintroduce it.

tests/test_docread.py covers the conversion, the cache and the observation; this file covers
only where the conversion may land. It is its own file because that one is at its size ceiling.
"""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from helpers import server_config
from rsched import ownseal, sandbox, utils_run
from rsched.engine import docread, fileops
from rsched.paths import within
from test_docread import _ctx, _pdf
from test_landlock import _base_spec, _run_jailed, needs_landlock

MARKDOWN = "<!-- page 1 of 1 -->\nClause one applies.\n"

#: The util's own `--out` write, as `doc-read.write_atomic` does it: a temp file BESIDE the
#: target, then a rename over it. The parent directory is what needs the CREATE right.
WRITE_BESIDE = """
import os, sys, tempfile
target = sys.argv[1]
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target),
                           prefix="." + os.path.basename(target) + ".")
with os.fdopen(fd, "w") as fh:
    fh.write("converted")
os.replace(tmp, target)
"""


def _converter(routine_dir: Path, monkeypatch):
    """`utils_run.run_util` as the JAIL answers it — the one seam the engine's call passes
    through, so the `--out` path under test is the one `docread` really builds.

    The fake writes the Markdown the way the util does (`WRITE_BESIDE`) and REFUSES, with the
    kernel's own message, when `--out` lands inside a sealed child of the routine dir: that is
    what `ownseal.mount_own_dir` leaves the util able to do. `test_the_real_jail_…` below pins
    the same refusal against the real kernel.
    """
    state = SimpleNamespace(outs=[])

    def fake(home, name, args, **kw):
        out = Path(args[args.index("--out") + 1])
        state.outs.append(out)
        for sealed in (routine_dir / n for n in ownseal.SEALED_DIRS):
            if within(sealed, out):
                return 1, "", (f"error: could not convert {args[0]}: [Errno 13] Permission "
                               f"denied: '{out}'")
        out.write_text(MARKDOWN, encoding="utf-8")
        return 0, json.dumps({"source": args[0], "format": "pdf", "pages": [1, 1],
                              "pages_total": 1, "capped": False, "next_page": None,
                              "no_text_layer": [], "blank": [], "page_lines": [[1, 1]],
                              "chars": len(MARKDOWN), "out": str(out)}), ""

    monkeypatch.setattr(docread.utils_run, "run_util", fake)
    monkeypatch.setattr(docread.utils_run, "util_needs",
                        lambda home, name: utils_run.UtilNeeds(set(), True, set(), True, ()))
    monkeypatch.setattr(docread.utils_lib, "exists", lambda home, name: name == "doc-read")
    monkeypatch.setattr(docread.sandbox, "policy_for_ctx", lambda ctx: "the run's policy")
    return state


def _run_ctx(tmp_path: Path):
    ctx = _ctx(tmp_path)
    ctx.aborted = lambda: False
    ctx.server = server_config(libraries_home=tmp_path / "libraries")
    return ctx


def test_the_conversion_destination_is_outside_every_sealed_dir(tmp_path, monkeypatch):
    """`--out` may not name a path inside ANY sealed child of the routine dir, and its parent
    must be somewhere the util jail actually mounts read-write.

    Both halves matter. The first is the defect: `.doc_cache/` is sealed, so the engine's own
    cache directory is invisible to the process it asks to fill it. The second is what keeps a
    fix honest — `sandbox._toolchain` mounts `tempfile.gettempdir()` read-write for EVERY util
    on every run, so a destination under it is legal for the call that must write there.
    """
    state = _converter(tmp_path, monkeypatch)
    _pdf(tmp_path / "fresh.pdf", ["Clause one applies."])
    docread.converted(_run_ctx(tmp_path), tmp_path / "fresh.pdf", "pdf")
    assert len(state.outs) == 1
    out = state.outs[0]
    for name in ownseal.SEALED_DIRS:
        assert not within(tmp_path / name, out), f"--out lands in the sealed {name}/"
    assert not within(tmp_path, out), "--out lands inside the routine dir, which is not writable"
    rw = sandbox._toolchain()[1]
    assert any(within(Path(root), out) for root in rw), (
        f"{out} is under none of the jail's read-write roots {rw}")


def test_a_never_converted_document_reads_as_markdown(tmp_path, monkeypatch):
    """The whole point, over a document this routine has never converted: `read_file` returns
    Markdown instead of the binary refusal with a `Permission denied` appended.
    """
    state = _converter(tmp_path, monkeypatch)
    _pdf(tmp_path / "contract.pdf", ["Clause one applies."])
    ctx = _run_ctx(tmp_path)
    obs = fileops._read_one("contract.pdf", {}, ctx)
    assert "error" not in obs, obs
    assert obs["content"].splitlines() == MARKDOWN.splitlines()   # the window carries no \n tail
    assert obs["converted"]["from"] == "pdf"
    cache = tmp_path / docread.CACHE_DIR
    assert sorted(p.suffix for p in cache.iterdir()) == [".json", ".md"]
    assert not state.outs[0].parent.exists(), "the conversion's drop directory was left behind"
    assert fileops._read_one("contract.pdf", {}, ctx)["content"] == obs["content"]
    assert len(state.outs) == 1, "the second read was not served from the cache"


@needs_landlock
def test_the_real_jail_refuses_the_cache_and_permits_the_drop_dir(make_routine, tmp_path):
    """The kernel's own answer, with the own-dir mount shape the run's policy composes.

    No blanket `/tmp` in the spec — pytest's `tmp_path` lives there, and a jail that mounted it
    wholesale would let the sealed write succeed and prove nothing (which is exactly why the
    end-to-end conversion test in tests/test_docread.py stayed green through the defect).
    """
    d = make_routine()
    (d / docread.CACHE_DIR).mkdir()
    drop = tmp_path / "drop"
    drop.mkdir()
    ro: list[str] = []
    rw: list[str] = []
    ls: list[str] = []
    ownseal.mount_own_dir(sandbox.SandboxPolicy(mode="permissive", own_dir=d), ro, rw, ls)
    spec = _base_spec(str(drop), *rw)
    spec["ro"] += ro
    spec["ls"] = ls
    sealed = _run_jailed(spec, WRITE_BESIDE, str(d / docread.CACHE_DIR / "x.md"))
    assert sealed.returncode != 0 and "PermissionError" in sealed.stderr
    legal = _run_jailed(spec, WRITE_BESIDE, str(drop / "x.md"))
    assert legal.returncode == 0, legal.stderr
    assert (drop / "x.md").read_text(encoding="utf-8") == "converted"
    assert tempfile.gettempdir() not in spec["rw"]       # the hole this test refuses to have
