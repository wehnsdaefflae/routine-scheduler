"""Where uv may put a MANAGED PYTHON — the default every jailed subprocess inherits.

R3631 (llmsectest-weekday, 2026-11-20): a run could not obtain a second interpreter.
`uv venv --python 3.10` dies because `~/.local/share/uv` does not exist and
`/home/<user>/.local/share` is root-owned, so nothing inside the jail can create it —
`sandbox._HOME_RW` lists the path, but `landlock` skips a rule path that does not exist and the
toolchain list has no bootstrap of its own. `~/.cache` IS owned by the daemon user and IS a
jail-RW toolchain root, and uv creates a missing install dir itself, so the repair is a default.
Measured on the host before the change: with `UV_PYTHON_INSTALL_DIR` under `.cache/uv/python`,
`uv venv --python 3.10` downloaded CPython 3.10.21 and produced a working interpreter.
"""

from pathlib import Path

from rsched import utils_lib, utils_verbs, uvtoolchain

UTIL = (
    "# /// script\n# dependencies = []\n# ///\n"
    '"""plainish — fixture util declaring nothing.\n\n'
    "usage:\n  gu plainish list [--json]\n"
    "calls: (none)\nsecrets: (none)\ntags: t\nnet: none\nfs: none\n"
    '"""\nprint("ok")\n'
)


def test_the_managed_python_root_is_under_the_writable_cache():
    """`.cache` is the one jail-RW toolchain root the daemon user owns. The XDG default
    (`.local/share/uv/python`) is the unwritable one R3631 died on."""
    home = Path("/home/someone")
    assert uvtoolchain.install_dir(home) == "/home/someone/.cache/uv/python"
    assert ".local/share" not in uvtoolchain.install_dir(home)


def test_a_value_already_set_wins():
    """A DEFAULT, never an override: the operator or a test may put the toolchain elsewhere."""
    kept = uvtoolchain.with_defaults({"UV_PYTHON_INSTALL_DIR": "/opt/pythons"})
    assert kept["UV_PYTHON_INSTALL_DIR"] == "/opt/pythons"
    # an EMPTY value is not a choice — it would leave uv on its unwritable default
    filled = uvtoolchain.with_defaults({"UV_PYTHON_INSTALL_DIR": "", "HOME": "/home/someone"})
    assert filled["UV_PYTHON_INSTALL_DIR"] == "/home/someone/.cache/uv/python"


def test_the_default_follows_the_env_s_own_home():
    """A jailed child's HOME is the one its policy gave it; the toolchain root must land inside
    THAT home, not the daemon's."""
    out = uvtoolchain.with_defaults({"HOME": "/home/jailed", "PATH": "/usr/bin"})
    assert out["UV_PYTHON_INSTALL_DIR"] == "/home/jailed/.cache/uv/python"
    assert out["PATH"] == "/usr/bin"          # nothing else is touched


def test_every_util_subprocess_inherits_it(tmp_path, monkeypatch):
    """The seam, not the helper: `utils_verbs.child_env` composes every util's environment."""
    utils_lib.ensure_library(tmp_path)
    utils_lib.write_util_file(tmp_path, "plainish", UTIL)
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)
    env = utils_verbs.child_env(tmp_path, "plainish", ["list"])
    assert env["UV_PYTHON_INSTALL_DIR"].endswith("/.cache/uv/python")


def test_the_scripts_venv_build_uses_the_same_default():
    """The per-routine scripts venv is the OTHER uv call site — `uv venv` / `uv pip install` in
    `scriptenv.ensure_env`, which starts from this process's environment rather than a scoped
    one. Both runners must agree, or a script gets a toolchain a util does not."""
    source = Path("src/rsched/scriptenv.py").read_text()
    assert "uvtoolchain.defaulted_environ()" in source
    assert "env=dict(os.environ)" not in source
    assert uvtoolchain.defaulted_environ()["UV_PYTHON_INSTALL_DIR"].endswith("/.cache/uv/python")
