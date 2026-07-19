"""Guards the tests/ui auto-rerun wiring: the `flaky` marker registered in pyproject.toml plus
the `pytest_collection_modifyitems` hook in tests/ui/conftest.py. The wiring absorbs the F120
xdist flakiness of the browser suite once pytest-rerunfailures is installed in the venv; until
then the marker is registered-but-inert (so this stays warning-clean under filterwarnings=error).
"""
from __future__ import annotations


def test_ui_items_are_marked_flaky(request):
    """This test lives under tests/ui, so the conftest collection hook must have applied the
    `flaky` marker to it. Asserting that here proves the marker is registered (no unknown-mark
    warning → error) and the hook scopes correctly to this directory — with no dependency on
    pytest-rerunfailures actually being installed."""
    marker = request.node.get_closest_marker("flaky")
    assert marker is not None, "tests/ui conftest hook did not apply the flaky marker"
    assert marker.kwargs.get("reruns") == 2, marker.kwargs
