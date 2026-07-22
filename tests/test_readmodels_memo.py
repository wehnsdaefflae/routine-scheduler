"""The read-model caching discipline: stat-fingerprint memo + the shared usage-stream
parser. A cache hit must be invisible (equal values, isolated copies); any input change
— append, atomic rewrite, deletion — must miss."""

import json

from rsched.readmodels import memo
from rsched.readmodels.usage_stream import usage_records


def test_memoized_hits_until_any_input_changes(tmp_path):
    memo.reset()
    f = tmp_path / "a.jsonl"
    f.write_text("one\n", encoding="utf-8")
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls), "rows": [1, 2]}

    v1 = memo.memoized("k", [f], compute)
    v2 = memo.memoized("k", [f], compute)
    assert v1 == v2 and len(calls) == 1          # hit — compute ran once
    v2["rows"].append(99)                        # a caller mutating its copy…
    assert memo.memoized("k", [f], compute)["rows"] == [1, 2]   # …never poisons the cache
    f.write_text("one\ntwo\n", encoding="utf-8")  # append/size change → miss
    assert memo.memoized("k", [f], compute)["n"] == 2
    f.unlink()                                    # deletion → miss
    assert memo.memoized("k", [f], compute)["n"] == 3


def test_usage_records_parse_once_and_refresh_on_append(tmp_path):
    memo.reset()
    ctrl = tmp_path / ".control"
    ctrl.mkdir(parents=True)
    stream = ctrl / "workflow-usage.jsonl"
    stream.write_text(json.dumps({"routine": "a", "tokens": 5}) + "\nnot json\n",
                      encoding="utf-8")
    first = usage_records(tmp_path)
    assert [r["routine"] for r in first] == ["a"]          # bad line skipped
    assert usage_records(tmp_path) is first                # shared value — no re-parse
    with stream.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"routine": "b", "tokens": 7}) + "\n")
    assert [r["routine"] for r in usage_records(tmp_path)] == ["a", "b"]
    assert usage_records(tmp_path / "ghost-home") == []    # missing stream → empty
