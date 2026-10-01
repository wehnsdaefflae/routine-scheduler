"""The images this deployment builds and pulls, and the rsched service's health probe — read as
files, because there is no CI and the images are built on the host, so whatever can be held
without a Docker daemon is held here.

Four promises, each one an edit can break with nothing failing until a rebuild on the host does:

1. EVERY IMAGE A BUILD READS IS PINNED BY DIGEST and keeps its tag beside it. A tag moves, so a
   rebuild took whatever had been pushed that day and two builds of one commit could differ with
   nothing in the repository to say why. Each FROM is pinned, a `COPY --from` may name only a
   stage (or it would pull an image no FROM pins), and the images compose pulls are pinned too.
2. Every RUN is BASH WITH PIPEFAIL (hadolint DL4006). Under /bin/sh the NodeSource
   `curl … | bash -` this image used to run exited 0 on a failed download.
3. NODE 24 LTS, from exactly ONE place: the official image's own install, copied out. The claude
   CLI's package declares `engines.node >=22`, and a second Node from apt would put two versions
   behind one name — which one a caller gets would depend on its PATH.
4. The HEALTHCHECK probes a path the real app serves WITHOUT a token — no credential belongs in
   compose or the image — on the port compose publishes, with a tool the image installs, and
   gives a whole boot (re-sync, migrations, seed sync) before a miss counts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched.web.app import create_app

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "docker-compose.yml"
ENGINE = REPO / "Dockerfile"
DOCKERFILES = sorted({ENGINE, *(REPO / "deploy").glob("Dockerfile*")})
#: `name:tag@sha256:<64 hex>` — the tag for the reader, the digest for Docker.
PINNED = re.compile(r"^(?P<name>[a-z0-9][a-z0-9._/-]*):(?P<tag>\w[\w.-]{0,127})"
                    r"@sha256:[0-9a-f]{64}$")
PIPEFAIL_SHELL = ["/bin/bash", "-o", "pipefail", "-c"]
#: The decision of 2026-10 (Node 24 LTS); the next LTS is a decision too, made here and in the
#: Dockerfile together.
NODE_MAJOR = "24"


def _instructions(path: Path) -> list[tuple[str, str]]:
    """(KEYWORD, arguments) per instruction, the way Docker's parser reads them: comment lines are
    dropped — even inside a continued RUN, which these files use — and `\\` continuations joined."""
    found: list[tuple[str, str]] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        keyword, _, args = (pending + line).partition(" ")
        found.append((keyword.upper(), args.strip()))
        pending = ""
    return found


def _stages(path: Path) -> list[dict]:
    """Each build stage: what its FROM names, the stage's own name, and its instructions."""
    stages: list[dict] = []
    for keyword, args in _instructions(path):
        if keyword == "FROM":
            words = [w for w in args.split() if not w.startswith("--")]   # --platform=…
            named = len(words) == 3 and words[1].upper() == "AS"
            stages.append({"ref": words[0], "name": words[2].lower() if named else None,
                           "body": []})
        elif stages:        # an ARG before the first FROM belongs to no stage
            stages[-1]["body"].append((keyword, args))
    return stages


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _seconds(duration: str) -> float:
    """A compose duration ("30s", "5m", "1m30s") in seconds."""
    units = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", duration)
    assert parts and "".join(n + u for n, u in parts) == duration, f"not a duration: {duration}"
    return sum(float(n) * units[u] for n, u in parts)


def test_this_file_checks_every_dockerfile_compose_builds():
    """A new service built from a Dockerfile elsewhere would otherwise escape every check below."""
    built = set()
    for spec in _compose()["services"].values():
        build = spec.get("build")
        if build is not None:
            context, dockerfile = ((build, "Dockerfile") if isinstance(build, str) else
                                   (build.get("context", "."), build.get("dockerfile", "Dockerfile")))
            built.add((REPO / context / dockerfile).resolve())
    assert built == set(DOCKERFILES), f"compose builds {sorted(built)}, this file checks {DOCKERFILES}"


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
def test_every_base_image_is_pinned_by_digest_and_keeps_its_tag(dockerfile):
    names: set[str] = set()
    for stage in _stages(dockerfile):
        ref = stage["ref"]
        assert ref.lower() in names or PINNED.match(ref), (
            f"{dockerfile.name}: FROM {ref} — pin it as <name>:<tag>@sha256:<index digest>, the "
            "tag kept for the reader (deploy/DOCKER.md, 'The image', says how to resolve one)")
        if stage["name"]:
            names.add(stage["name"])


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
def test_copy_from_names_a_stage_never_an_image(dockerfile):
    """`COPY --from=<image>` pulls that image with no FROM line to pin it — and when a stage is
    deleted, a COPY naming it by an image's name silently pulls that image's `latest`."""
    names: set[str] = set()
    for stage in _stages(dockerfile):
        for keyword, args in stage["body"]:
            sources = re.findall(r"--from=(\S+)", args) if keyword in ("COPY", "ADD") else []
            unnamed = [s for s in sources if s.lower() not in names and not s.isdigit()]
            assert not unnamed, f"{dockerfile.name}: {keyword} --from={unnamed} names no stage"
        if stage["name"]:
            names.add(stage["name"])


def test_every_image_compose_pulls_is_pinned():
    services = _compose()["services"]
    pulled = {name: spec["image"] for name, spec in services.items()
              if "image" in spec and "build" not in spec}
    assert pulled, "compose pulls no image — the cliproxy pin this checks has moved"
    loose = {name: image for name, image in pulled.items() if not PINNED.match(image)}
    assert not loose, f"pulled by a tag that moves: {loose}"


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
def test_every_shell_form_run_is_bash_with_pipefail(dockerfile):
    """SHELL is per STAGE: a new FROM starts again from /bin/sh, so each stage that runs anything
    sets it before its first RUN. An exec-form RUN (a JSON array) runs no shell at all."""
    runs = 0
    for stage in _stages(dockerfile):
        shell = None
        for keyword, args in stage["body"]:
            if keyword == "SHELL":
                shell = json.loads(args)
            elif keyword == "RUN" and not args.startswith("["):
                runs += 1
                assert shell == PIPEFAIL_SHELL, (
                    f"{dockerfile.name}: RUN {args[:60]}… runs under {shell or '/bin/sh'}; "
                    f"set SHELL {json.dumps(PIPEFAIL_SHELL)} before it")
    assert runs, f"{dockerfile.name} runs nothing — the check above checked nothing"


def test_node_is_24_lts_copied_from_the_official_image_and_nowhere_else():
    stages = _stages(ENGINE)
    node = []
    for stage in stages:
        pin = PINNED.match(stage["ref"])
        if pin and pin["name"].removeprefix("docker.io/").removeprefix("library/") == "node":
            node.append((stage["name"], pin["tag"]))
    assert len(node) == 1 and node[0][0], "the engine image takes Node from ONE named stage"
    stage_name, tag = node[0]
    assert re.split(r"[.-]", tag)[0] == NODE_MAJOR, f"node:{tag} is not Node {NODE_MAJOR} LTS"
    final = stages[-1]["body"]
    copied = {args.split()[-1] for keyword, args in final
              if keyword == "COPY" and f"--from={stage_name}" in args}
    assert "/usr/local/bin/node" in copied and "/usr/local/lib/node_modules" in copied, copied
    # a second source would put another Node on PATH: NodeSource's repo, or Debian's own package
    runs = " && ".join(args for keyword, args in final if keyword == "RUN")
    assert "nodesource" not in runs.lower(), "Node comes from the node-dist stage, not NodeSource"
    for step in runs.split("&&"):
        if "apt-get install" in step:
            assert not {"nodejs", "npm"} & set(step.split()), f"apt installs a second Node: {step}"


def _healthcheck() -> dict:
    check = _compose()["services"]["rsched"].get("healthcheck")
    assert check and not check.get("disable"), "the rsched service has no healthcheck"
    return check


def _probe_url() -> str:
    test = _healthcheck()["test"]
    assert test[0] == "CMD", "exec form: the probe runs no shell, so nothing is interpolated"
    urls = [arg for arg in test if arg.startswith("http")]
    assert len(urls) == 1, test
    return urls[0]


def test_the_probed_path_answers_without_a_token(tmp_path):
    """The probe carries no credential, so the path it asks for must be one the app serves to
    anybody — checked through the real auth rules, with auth ON (the 401 says so)."""
    path = urlparse(_probe_url()).path
    with TestClient(create_app(make_test_server(tmp_path), with_scheduler=False)) as client:
        assert client.get("/api/status").status_code == 401
        answer = client.get(path)
    assert answer.status_code == 200, f"{path} answers {answer.status_code} without a token"


def test_the_probe_carries_no_credential_and_uses_a_tool_the_image_installs():
    test = _healthcheck()["test"]
    assert not re.search(r"authorization|bearer|token", " ".join(test), re.IGNORECASE), test
    # without --fail curl exits 0 on a 401 or a 500: a probe that cannot fail
    assert test[1] == "curl" and "--fail" in test, test
    installed = set()
    for keyword, args in _stages(ENGINE)[-1]["body"]:
        if keyword == "RUN":
            for step in args.split("&&"):
                if "apt-get install" in step:
                    installed |= set(step.split())
    assert test[1] in installed, f"the probe runs {test[1]}, which the engine image never installs"


def test_the_probe_reaches_the_port_compose_publishes():
    service = _compose()["services"]["rsched"]
    url = urlparse(_probe_url())
    published = {str(p).rsplit(":", 1)[-1] for p in service["ports"]}
    assert url.hostname == "127.0.0.1" and str(url.port) in published, (url, published)
    # a loopback probe needs a daemon that listens there too
    assert service["environment"]["RSCHED_BIND"] == "0.0.0.0"  # noqa: S104 — the value under test
    # …and must not be handed to an egress proxy the container's environment names: curl then
    # dials the proxy instead, and the service reads unhealthy for as long as the proxy is set
    test = _healthcheck()["test"]
    assert "--noproxy" in test and test[test.index("--noproxy") + 1] == "*", test


def test_the_probe_gives_a_boot_time_to_finish():
    """The port opens only after the boot sequence — `uv run`'s re-sync, the one-shot
    migrations, the seed sync, the library adoption (a clone, on a fresh host). Misses inside
    `start_period` do not count; after it, one slow answer is not a verdict. A field left out
    takes Docker's own default, which is what a deleted line would leave behind."""
    check = _healthcheck()
    interval = _seconds(check.get("interval", "30s"))
    timeout = _seconds(check.get("timeout", "30s"))
    assert _seconds(check.get("start_period", "0s")) >= 120, "a booting daemon would read unhealthy"
    assert timeout < interval, "a probe must end before the next one starts"
    assert check.get("retries", 3) >= 2, "one slow answer would read unhealthy"
    test = check["test"]
    if "--max-time" in test:   # curl gives up first, so the health log says what curl saw
        assert float(test[test.index("--max-time") + 1]) < timeout
