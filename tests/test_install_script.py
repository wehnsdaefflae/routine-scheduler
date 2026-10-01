"""`deploy/install.sh`, run for real against a throwaway HOME.

The host install is a shell script, so nothing imported it and nothing checked it — and it had
kept its own copy of config generation, which drifted from `bootstrap.ensure_config` the day
the routine tier got its own token. Here it runs end to end with the three things that would
reach outside the sandbox stubbed: `uv` (its `sync` is a no-op and its `run python` is this
suite's interpreter, so the script's Python calls run the real package), `systemctl` and
`loginctl`. HOME is the test's own, so the config, the unit and every data home land in tmp.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from helpers import write_executable

REPO = Path(__file__).resolve().parents[1]
#: The example's placeholders — a value either tier ever carries from these is a known token.
PLACEHOLDERS = {"change-me", "change-me-too", ""}

UV_STUB = """#!/bin/sh
case "$1" in
  sync) exit 0 ;;
  run) shift; [ "$1" = python ] && shift; exec "$RSCHED_TEST_PYTHON" "$@" ;;
esac
echo "unexpected uv call: $*" >&2
exit 2
"""
# `is-active` answers "inactive" so the script's Ollama probe stays quiet; everything else
# (daemon-reload, enable --now, status) succeeds and is logged for the assertions.
SYSTEMCTL_STUB = """#!/bin/sh
echo "$*" >> "$HOME/systemctl.log"
case " $* " in *" is-active "*) exit 3 ;; esac
exit 0
"""


@pytest.fixture
def host(tmp_path):
    """A HOME with the checkout where the unit expects it (a symlink to this repo), uv where
    the unit runs it, and an existing library dir so seeding — tested elsewhere — is skipped."""
    home = tmp_path / "home"
    (home / "git-repos").mkdir(parents=True)
    (home / "git-repos/routine-scheduler").symlink_to(REPO)
    (home / ".local/share/routine-scheduler-libraries").mkdir(parents=True)
    write_executable(home / ".local/bin/uv", UV_STUB)
    bin_dir = tmp_path / "bin"
    write_executable(bin_dir / "systemctl", SYSTEMCTL_STUB)
    write_executable(bin_dir / "loginctl", "#!/bin/sh\nexit 0\n")
    write_executable(bin_dir / "pgrep", "#!/bin/sh\nexit 1\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("RSCHED_")}
    env.update(HOME=str(home), PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
               RSCHED_TEST_PYTHON=sys.executable)
    return home, env


def _install(home: Path, env: dict, script: Path | None = None) -> subprocess.CompletedProcess:
    script = script or home / "git-repos/routine-scheduler/deploy/install.sh"
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                          timeout=120, check=False)


def _config(home: Path) -> dict:
    return yaml.safe_load((home / ".config/routine-scheduler/config.yaml").read_text())


def test_a_fresh_install_gives_both_tiers_a_random_token(host):
    """The routine tier is injected into every run as RSCHED_API_TOKEN. The script's own
    config copy replaced only `token:`, so every host install kept the example's
    `routine_token` — a credential anyone can read in this repository."""
    home, env = host
    proc = _install(home, env)
    assert proc.returncode == 0, proc.stderr
    cfg = _config(home)
    assert cfg["token"] not in PLACEHOLDERS
    assert cfg["routine_token"] not in PLACEHOLDERS
    assert cfg["token"] != cfg["routine_token"]
    assert f"token: {cfg['token']}" in proc.stdout
    assert (home / ".config/systemd/user/routine-scheduler.service").is_file()
    assert "enable --now routine-scheduler.service" in (home / "systemctl.log").read_text()


def test_a_rerun_after_a_settings_save_completes(host):
    """Safe to re-run after every git pull — including after the Settings page saved the
    config, which rewrites it as YAML with the token unquoted. The quoted-token grep the
    script ended on matched nothing then, and `set -e` failed the whole re-run."""
    home, env = host
    assert _install(home, env).returncode == 0
    path = home / ".config/routine-scheduler/config.yaml"
    cfg = _config(home)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")   # a web save
    assert f"token: {cfg['token']}\n" in path.read_text()                       # unquoted
    proc = _install(home, env)
    assert proc.returncode == 0, proc.stderr
    assert "config exists:" in proc.stdout
    assert f"token: {cfg['token']}" in proc.stdout
    assert _config(home)["routine_token"] == cfg["routine_token"]              # kept


@pytest.mark.parametrize("broken", ["checkout elsewhere", "no uv where the unit runs it"])
def test_an_install_the_unit_cannot_run_is_refused(host, broken):
    """The unit pins the checkout to ~/git-repos/routine-scheduler and uv to ~/.local/bin/uv.
    Either one missing used to end in "done" and a service that could not start."""
    home, env = host
    script = None
    if broken == "checkout elsewhere":
        (home / "git-repos/routine-scheduler").unlink()   # a symlink there would BE the checkout
        script = REPO / "deploy/install.sh"
    else:
        (home / ".local/bin/uv").unlink()
    proc = _install(home, env, script)
    assert proc.returncode != 0
    assert ("git-repos/routine-scheduler" if script else ".local/bin/uv") in proc.stderr
    assert not (home / ".config/systemd/user/routine-scheduler.service").exists()
    assert not (home / ".config/routine-scheduler/config.yaml").exists()
