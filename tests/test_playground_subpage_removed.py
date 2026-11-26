"""F959 — the Playground CONSOLE SUBPAGE is gone, and only the page's own half went with it.
The operator's order (2026-11-25) was "Remove the playground subpage. We don't need it after
all", so this file asserts the ABSENCE of the page, its HTTP surface, its served flag and the
modules nothing but that page reached — and, in the same breath, the PRESENCE of what the page
was not: the engine's in-run experimental seam (`engine/tails.py` ->
`playground.runtime.at_observation`, gated by a conversation's own `playground: true` block) and
the stored-history reader beside it. Written as absence assertions on purpose: a removal that
leaves a route registered, a status key served or a nav id in the markup is a page that comes
back the moment something fetches it.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from rsched.config.server import ServerConfig

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"


def test_the_page_and_its_bench_are_gone():
    present = [name for name in ("views/playground.js", "views/playground-bench.js")
               if (STATIC / name).exists()]
    assert present == [], f"the console subpage is still on disk: {present}"


def test_the_front_end_carries_no_navigation_and_no_route():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "nav-playground" not in index
    assert 'data-nav="playground"' not in index
    assert "playground" not in app_js.lower(), "app.js still resolves or labels the subpage"


def test_the_http_surface_is_gone():
    assert not (REPO / "src" / "rsched" / "web" / "api_playground.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("rsched.web.api_playground")


def test_the_app_resolves_no_playground_route(api_client):
    client, _ = api_client
    still_there = [p for p in (getattr(r, "path", "") for r in client.app.routes)
                   if "playground" in p]
    assert still_there == [], f"routes still registered: {still_there}"
    assert client.get("/api/playground/features").status_code == 404
    assert client.get("/api/playground/conversations").status_code == 404
    assert client.post("/api/playground/bench", json={}).status_code == 404


def test_no_served_flag_can_resurrect_the_page(api_client):
    """The flag gated the page and nothing else, so it goes with it — field AND status key."""
    client, _ = api_client
    assert "playground_enabled" not in client.get("/api/status").json()
    assert "playground_enabled" not in ServerConfig.model_fields


def test_only_the_pages_half_of_the_lab_went():
    """`vectorstore` and the embeddings backend were reached from the bench alone."""
    from rsched.playground import scoring, stores

    assert hasattr(scoring, "LexicalBackend"), "the engine's baseline backend must stay"
    assert hasattr(stores, "load_all"), "the shared condition reader must stay"
    assert not hasattr(scoring, "EmbeddingBackend"), "the bench's backend outlived the bench"
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("rsched.playground.vectorstore")


def test_the_engines_in_run_seam_is_not_part_of_the_removal(tmp_path):
    from rsched.engine import tails
    from rsched.playground import features, runtime

    assert tails.playground_runtime is runtime
    assert callable(runtime.at_observation)
    # the stored-history reader an existing flagged conversation's runs wrote through
    assert features.firings_path(tmp_path).name == "playground-firings.jsonl"


def test_an_existing_flagged_conversation_still_runs_its_experiments(tmp_path):
    """Stored conversations are preserved, flag block included: the page is gone, the lab is not."""
    import yaml

    from rsched.playground import features

    (tmp_path / "routine.yaml").write_text(
        yaml.safe_dump({"playground": {"on": True, "features": ["util-hint"]}}),
        encoding="utf-8")

    class Cfg:
        dir = tmp_path

    assert [f.slug for f in features.enabled_features(Cfg())] == ["util-hint"]
