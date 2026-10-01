"""The four-folder messages read model (`readmodels/messages.py`) called directly; the GET
route over it is covered in test_api.py."""

from __future__ import annotations

import json

from rsched.readmodels import messages


def _msg(path, ts, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"text": text, "ts": ts}), encoding="utf-8")


def test_a_folder_is_newest_first_by_the_moment_each_message_names(tmp_path):
    """A filename is no clock. The KEYED names — a report delivery's `msg-rep-<id>`, a
    background result's `msg-bg-<task>` — sort after every timestamped `msg-<ts>-…` as text,
    so a delivery hours older than a note sat at the top of the folder."""
    routine = tmp_path / "apir"
    _msg(routine / "inbox" / "msg-rep-R7.json", "2026-10-01T07:00:00+02:00", "older delivery")
    _msg(routine / "inbox" / "msg-2026-10-01T080000+0200-abcd1234.json",
         "2026-10-01T08:00:00+02:00", "newer note")
    consumed = routine / "runs" / "20261001-060000" / "consumed"
    _msg(consumed / "msg-bg-apir-12345678.json", "2026-10-01T06:00:00+02:00", "older result")
    _msg(consumed / "msg-2026-10-01T070000+0200-ef012345.json", "2026-10-01T07:00:00+02:00",
         "newer answer")

    folders = messages.build(routine, tmp_path)
    assert [m["text"] for m in folders["inbox"]] == ["newer note", "older delivery"]
    assert [m["text"] for m in folders["read"]] == ["newer answer", "older result"]
