"""The per-run cost profile's ROUTE (D163): `GET /api/changes/{slug}/cost`.

`readmodels/cost_profile.py` shipped in 0.495.0 reading the per-RUN cost of a routine, and
nothing imported it but its own test — a reader the operator who asked for it could not see.
It rides `api_changes.py` rather than a module of its own because the DEVELOPMENT view is
where it is read (#/changes/<slug>, beside Outcome, Model fit and Recipe health) and that
module already serves that view's other per-routine read models through one `_routine_home`
guard.

What is pinned here: the route answers the reader's own shape, a window the reader calls
MIXED is served AS mixed (the console cannot warn about what the payload drops), and an
unknown slug is a 404 like every sibling.
"""

from __future__ import annotations

from helpers import measured_run, write_usage_stream

SLUG = "costr"


def _understated(recs: list[dict]) -> list[dict]:
    """The R3112 symptom on the newest run: a depth-0 parent reporting LESS volume than its
    own `#sub<n>` child row, which is understatement by arithmetic whatever release wrote it.
    """
    parent = recs[-1]
    return [*recs, {**parent, "run_id": f"{parent['run_id']}#sub1", "depth": 1,
                    "volume": 9_000_000}]


def test_the_route_serves_the_per_run_cost_profile(api_client, make_routine):
    """The figure a budget is set in, over every run — served under the reader's own keys."""
    client, tmp = api_client
    make_routine(slug=SLUG)
    write_usage_stream(tmp / "routines",
                       [measured_run(i, slug=SLUG, volume=4_000_000) for i in range(1, 6)])

    res = client.get(f"/api/changes/{SLUG}/cost")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slug"] == SLUG
    assert body["runs"] == 5
    assert body["typical"]["volume_median"] == 4_000_000
    assert set(body) >= {"slug", "runs", "typical", "baseline", "recent", "trend",
                         "mixed_accounting", "integrity"}
    assert body["mixed_accounting"] == []
    assert body["integrity"]["understated_runs"] == 0


def test_a_mixed_window_is_served_as_mixed(api_client, make_routine):
    """The one way this surface can lie is by dropping the integrity verdict: a console that
    never receives `mixed_accounting` renders a mixture of two quantities as one median.
    """
    client, tmp = api_client
    make_routine(slug=SLUG)
    recs = [measured_run(i, slug=SLUG, volume=4_000_000) for i in range(1, 11)]
    write_usage_stream(tmp / "routines", _understated(recs))

    body = client.get(f"/api/changes/{SLUG}/cost").json()
    assert body["mixed_accounting"] == ["recent"]
    assert body["integrity"]["understated_runs"] == 1
    assert body["recent"]["trustworthy"] is False
    assert "0.494.0" in body["integrity"]["detail"]


def test_an_unknown_routine_is_a_404(api_client):
    client, _tmp = api_client
    assert client.get("/api/changes/nosuchroutine/cost").status_code == 404
