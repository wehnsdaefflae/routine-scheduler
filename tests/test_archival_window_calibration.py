"""An archival candidate is fit-tested against the window THIS RUN measured, not the catalog's.

The operator, 2026-11-02: *"— background archive did not land — (history archival degraded:
otari: HTTP 400: prompt is too long: 1028908 tokens > 1000000 maximum)"*.

`estimate_input_tokens` is 3.5 UTF-8 bytes per token — a packing guess — so a run whose content
packs denser than that reads LIGHT, and `window.note_prompt_size` measures that gap on every
completion and keeps it as `loop._token_ratio`. `compact_if_needed` restates the foreground
model's window in those measured units (`_calibrated_window`) before any gate sees it.

`_pick_archival_model` then built its own candidates — the dedicated compaction model and the
tool-call model — FRESH from the registry, which serves the CATALOG figure. So the one ref that
had been corrected was the main model's, and the cheaper tiers (which are preferred, because
machine work belongs there) were fit-tested against a window the run had already measured as
smaller. A run reading ~1.5x light therefore posted an archival over the provider's hard limit.

Unlike a foreground turn, the archival thread has no shrink-and-retry net of its own
(`overflow._recover_oversize_prompt` serves the turn, not the thread), so that 400 is terminal:
the middle's navigable history is lost and the run is told only that the archive degraded.

Both directions are asserted, because the correction must bite exactly where the measurement
says so: the same middle and the same models ARCHIVE while nothing has been measured, and are
declined once the run has measured itself 1.6x light.
"""

from types import SimpleNamespace

from rsched.config import ModelRef
from rsched.engine import window

#: A million-token window — the shape the operator's failure actually happened on. The tool-call
#: candidate is the same model, so the ONLY difference between the two refs below is which of
#: them the picker corrects.
BIG = ModelRef(endpoint="ep", model="opus", name="opus",
               context_tokens=1_000_000, max_tokens=8_000)

#: Per message of the middle. 20 of these estimate ~600,000 tokens, which fits 0.7 x 1,000,000
#: (so the catalog window accepts the archival) and does not fit 0.7 x (1,000,000 / 1.6) (so a
#: run that has measured itself light must decline it).
MIDDLE_CHARS = 105_000


def _loop(monkeypatch, ratio: float, selected: list, events: list):
    """A run at the moment its prompt crosses the compaction gate."""
    ctx = SimpleNamespace(
        server=SimpleNamespace(compaction_model=""),
        routine=SimpleNamespace(models={}, efforts={}),
        usage={}, phase="", tokens_remaining=lambda: None,
        stage_coverage=lambda: {"declared": [], "entered": [], "skipped": []},
        registry=SimpleNamespace(for_model=lambda *_a: ("tool-endpoint", BIG)),
        transcript=SimpleNamespace(event=lambda kind, data: events.append(data)))
    messages = ([{"role": "user", "content": "x" * 1_000}] * 6
                + [{"role": "user", "content": "x" * MIDDLE_CHARS}] * 20
                + [{"role": "user", "content": "x" * 1_000}] * 24)
    loop = SimpleNamespace(ctx=ctx, messages=messages, turn_records=[], _last_compact_after=0,
                           _stage_mark=None, _token_ratio=ratio)
    monkeypatch.setattr(window, "_warn_before_eviction", lambda *_a: False)
    monkeypatch.setattr(window, "maybe_compact",
                        lambda msgs, *_a: (msgs[:6] + msgs[-24:], {"mode": "digest"}))
    monkeypatch.setattr(window.archival, "start",
                        lambda *a: selected.append(a[3].context_tokens))
    return loop


def test_an_unmeasured_run_archives_on_the_catalog_window(monkeypatch):
    """The green direction: nothing measured, nothing corrected — the archive is started."""
    selected, events = [], []
    window.compact_if_needed(_loop(monkeypatch, 1.0, selected, events), "main-endpoint", BIG)

    assert selected == [1_000_000]
    assert events[-1]["archival"] == "background"


def test_a_run_measured_light_declines_the_candidate_the_catalog_would_accept(monkeypatch):
    """The defect: the tool-call candidate was taken on its CATALOG window while the run had
    measured its own estimate 1.6x light, and the archival went over the provider's limit.
    Every candidate is now declined in the units the fit test measures in, and the
    deterministic digest stands for those turns."""
    selected, events = [], []
    window.compact_if_needed(_loop(monkeypatch, 1.6, selected, events), "main-endpoint", BIG)

    assert selected == []
    assert "no available model can archive it" in events[-1]["archival_skipped"]
    # and it reports the window it actually judged against, not the catalog's
    assert "1,000,000-token window" not in events[-1]["archival_skipped"]
