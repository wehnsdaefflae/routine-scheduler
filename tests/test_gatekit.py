"""The declarative gate checks: each answers "no work" only when it KNOWS there is none."""
import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rsched import gatekit
from rsched.config.routine import RunGateConfig
from rsched.daemon import gate_prepare
from rsched.gatekit import run as kit
from rsched.paths import atomic_write_json

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)


def ctx(tmp_path, checks, *, last_ok=None, routine="me"):
    return {"version": 2, "routine": routine, "routine_dir": str(tmp_path / "routine"),
            "routines_home": str(tmp_path), "now": NOW.isoformat(), "last_ok": last_ok,
            "checks": checks}


def ok_since(hours, fingerprints=None):
    return {"run_id": "me:x", "started": (NOW - timedelta(hours=hours)).isoformat(),
            "fingerprints": fingerprints or {}}


def one(result):
    assert len(result["checks"]) == 1
    return result["checks"][0]


@pytest.fixture
def routine(tmp_path):
    d = tmp_path / "routine"
    (d / "state").mkdir(parents=True)
    return d


def test_every_check_needs_a_baseline_or_reports_work(tmp_path, routine):
    """No earlier ok run means nothing to compare against — work, never a skip."""
    out = kit.evaluate(ctx(tmp_path, [{"kind": "max_quiet", "days": 3}]))
    assert out["decision"] == "run"
    assert "nothing to compare against" in one(out)["reason"]


def test_max_quiet_is_a_backstop(tmp_path, routine):
    fresh = kit.evaluate(ctx(tmp_path, [{"kind": "max_quiet", "days": 3}],
                                 last_ok=ok_since(24)))
    assert fresh["decision"] == "skip" and fresh["reason"].startswith("Nothing to do")
    stale = kit.evaluate(ctx(tmp_path, [{"kind": "max_quiet", "days": 3}],
                                 last_ok=ok_since(24 * 4)))
    assert stale["decision"] == "run"


def ok_started_now() -> dict:
    """A last ok run that started on the REAL clock, between files made before and after it —
    the filesystem stamps a file's inode with that clock, and `files_changed` reads it. The
    pauses clear the coarse clock the kernel stamps with."""
    time.sleep(0.05)
    started = datetime.now(UTC)
    time.sleep(0.05)
    return {"run_id": "me:x", "started": started.isoformat(), "fingerprints": {}}


def test_files_changed_compares_against_the_last_ok_run(tmp_path, routine):
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "old.txt").write_text("x")
    last_ok = ok_started_now()
    check = [{"kind": "files_changed", "paths": [str(watched)]}]
    assert kit.evaluate(ctx(tmp_path, check, last_ok=last_ok))["decision"] == "skip"
    (watched / "new.txt").write_text("y")          # written after the last ok run started
    assert kit.evaluate(ctx(tmp_path, check, last_ok=last_ok))["decision"] == "run"


def test_a_file_that_arrives_with_an_old_mtime_is_new(tmp_path, routine):
    """mv, `rsync -a`, an unpacked archive and every sync client keep a file's own mtime, so a
    photo taken last week and synced in today predates the last ok run by its mtime alone. Its
    inode changed when it arrived — that is what makes it new to the folder."""
    watched = tmp_path / "watched"
    watched.mkdir()
    last_ok = ok_started_now()
    arrived = watched / "photo.jpg"
    arrived.write_text("x")
    week_ago = (datetime.now(UTC) - timedelta(days=7)).timestamp()
    os.utime(arrived, (week_ago, week_ago))
    check = [{"kind": "files_changed", "paths": [str(watched)]}]
    assert kit.evaluate(ctx(tmp_path, check, last_ok=last_ok))["decision"] == "run"


def test_files_changed_nonempty_mode_ignores_time(tmp_path, routine):
    intake = tmp_path / "intake"
    intake.mkdir()
    check = [{"kind": "files_changed", "paths": [str(intake)], "nonempty": True}]
    assert kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1)))["decision"] == "skip"
    (intake / "doc.pdf").write_text("z")
    assert kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1)))["decision"] == "run"


def test_a_missing_folder_is_work_not_silence(tmp_path, routine):
    check = [{"kind": "files_changed", "paths": [str(tmp_path / "gone")]}]
    out = kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1)))
    assert out["decision"] == "run" and "could not check" in one(out)["reason"]


def test_state_pending(tmp_path, routine):
    check = [{"kind": "state", "file": "state/queue.json", "key": "pending"}]
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "skip"   # no file, no work
    atomic_write_json(routine / "state/queue.json", {"pending": []})
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "skip"
    atomic_write_json(routine / "state/queue.json", {"pending": [{"id": 1}]})
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "run"
    (routine / "state/queue.json").write_text("{not json")
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "run"   # unreadable = work


def test_url_changed_fingerprints_the_answer(tmp_path, routine):
    page = tmp_path / "feed.xml"
    page.write_text("<rss><item><guid>a</guid></item></rss>")
    check = [{"kind": "url_changed", "url": page.as_uri(), "select": "feed", "id": "feed"}]
    first = kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1)))
    assert first["decision"] == "run"                     # no fingerprint recorded yet
    fp = one(first)["fingerprint"]
    same = kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1, {"feed": fp})))
    assert same["decision"] == "skip"
    page.write_text("<rss><item><guid>a</guid></item><item><guid>b</guid></item></rss>")
    changed = kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1, {"feed": fp})))
    assert changed["decision"] == "run"


def test_an_answer_too_large_to_read_whole_is_work(tmp_path, routine):
    """The check reads at most 8 MiB; a page that changed only past that would fingerprint as
    "unchanged", so an answer over the limit is work rather than a guess."""
    page = tmp_path / "big.html"
    page.write_bytes(b"x" * (8 * 2**20) + b"<p>first</p>")
    check = [{"kind": "url_changed", "url": page.as_uri(), "id": "big"}]
    first = one(kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1))))
    page.write_bytes(b"x" * (8 * 2**20) + b"<p>second</p>")          # changed past the limit
    baseline = {"big": first.get("fingerprint") or "none recorded"}
    out = kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1, baseline)))
    assert out["decision"] == "run" and "8 MiB" in one(out)["reason"]


def test_url_changed_json_path(tmp_path, routine):
    api = tmp_path / "api.json"
    api.write_text(json.dumps({"meta": {"ts": 1}, "items": [1, 2]}))
    check = [{"kind": "url_changed", "url": api.as_uri(), "select": "json:items", "id": "a"}]
    fp = one(kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1))))["fingerprint"]
    api.write_text(json.dumps({"meta": {"ts": 2}, "items": [1, 2]}))   # only noise moved
    assert kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1, {"a": fp})))[
        "decision"] == "skip"


def test_repo_changed(tmp_path, routine):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "one"], check=True)
    check = [{"kind": "repo_changed", "path": str(repo), "id": "r"}]
    fp = one(kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1))))["fingerprint"]
    assert kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1, {"r": fp})))[
        "decision"] == "skip"
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "two"], check=True)
    assert kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1, {"r": fp})))[
        "decision"] == "run"


def test_runs_since_counts_other_routines_only(tmp_path, routine):
    usage = tmp_path / ".control" / "workflow-usage.jsonl"
    usage.parent.mkdir()
    rows = [{"routine": "me", "ts": NOW.isoformat()},
            {"routine": "other", "ts": (NOW - timedelta(hours=5)).isoformat()},
            {"routine": "child", "ts": NOW.isoformat(), "depth": 1}]
    usage.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    check = [{"kind": "runs_since", "min_runs": 1}]
    assert kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(2)))["decision"] == "skip"
    with usage.open("a") as fh:
        fh.write(json.dumps({"routine": "other", "ts": NOW.isoformat()}) + "\n")
    assert kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(2)))["decision"] == "run"


def test_weekdays(tmp_path, routine):
    today = NOW.weekday()
    due = [{"kind": "weekdays", "days": [today]}]
    assert kit.evaluate(ctx(tmp_path, due, last_ok=ok_since(30)))["decision"] == "run"
    assert kit.evaluate(ctx(tmp_path, due, last_ok=ok_since(1)))["decision"] == "skip"
    other = [{"kind": "weekdays", "days": [(today + 1) % 7]}]
    assert kit.evaluate(ctx(tmp_path, other, last_ok=ok_since(30)))["decision"] == "skip"


def test_mail_without_its_secret_is_work(tmp_path, routine, monkeypatch):
    monkeypatch.delenv("MAIL_USER", raising=False)
    check = [{"kind": "mail", "host": "imap.invalid", "user_secret": "MAIL_USER",
              "password_secret": "MAIL_PASS"}]
    out = kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1)))
    assert out["decision"] == "run" and "MAIL_USER" in one(out)["reason"]


def test_the_mailbox_login_goes_over_a_verified_tls_connection(tmp_path, routine, monkeypatch):
    """imaplib.IMAP4_SSL without a context accepts ANY certificate (its backwards-compatible
    default), so the password would go to whoever answered on the way to the mail server."""
    import imaplib
    import ssl

    seen = {}

    class Refused:
        def __init__(self, host, port, *, ssl_context=None, timeout=None):
            seen["tls"] = ssl_context
            raise OSError("connection refused")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", Refused)
    monkeypatch.setenv("MAIL_USER", "u")
    monkeypatch.setenv("MAIL_PASS", "p")
    check = [{"kind": "mail", "host": "imap.example", "user_secret": "MAIL_USER",
              "password_secret": "MAIL_PASS"}]
    assert kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1)))["decision"] == "run"
    tls = seen["tls"]
    assert tls is not None and tls.verify_mode == ssl.CERT_REQUIRED and tls.check_hostname


class Inbox:
    """Just enough IMAP for `_count`: `n` unread messages, only message `watched` (1 = the
    oldest) from the sender the watch list names."""

    def __init__(self, n: int, watched: int):
        self.n, self.watched = n, watched

    def select(self, _mailbox, readonly):
        assert readonly
        return "OK", [str(self.n).encode()]

    def search(self, _charset, criteria):
        return "OK", [b" ".join(str(i).encode() for i in range(1, self.n + 1))]

    def fetch(self, numbers, query):
        out: list = []
        for num in numbers.split(b","):
            sender = "Watched <w@x.example>" if int(num) == self.watched else "Bulk <b@y.example>"
            out += [(num + b" (BODY[HEADER.FIELDS (FROM SUBJECT)] {40}",
                     f"From: {sender}\r\nSubject: news\r\n\r\n".encode()), b")"]
        return "OK", out


def test_a_mailbox_too_full_to_read_whole_is_work():
    """Only the newest messages are read. When none of them counts and older ones went unread,
    the watched mail may be among those — that is not knowing, so it is work."""
    from rsched.gatekit.kit_net import UnknownError, _count

    watch = {"from_any": ["w@x.example"]}
    assert _count(Inbox(400, watched=1), "INBOX", watch, None, [], []) == 1
    assert _count(Inbox(900, watched=900), "INBOX", watch, None, [], []) == 1
    with pytest.raises(UnknownError, match="newest 500"):
        _count(Inbox(900, watched=1), "INBOX", watch, None, [], [])


def test_a_folded_or_encoded_header_is_matched_as_the_reader_sees_it():
    """A Subject folded onto a second line, or sent as an RFC 2047 encoded word (how a mail
    client carries an umlaut), is the same Subject; matching the raw first line skipped both —
    and an address folded under its display name escaped the sender lists."""
    import base64

    from rsched.gatekit.kit_net import _matches

    check = {"subject_any": ["ARDS"], "from_any": ["jürgen"]}
    folded = "From: A <a@x.example>\r\nSubject: Re: the long thread about\r\n the ARDS report\r\n"
    assert _matches(folded, check, [], [])
    word = base64.b64encode("Übersicht ARDS".encode()).decode()
    assert _matches(f"From: B <b@x.example>\r\nSubject: =?utf-8?b?{word}?=\r\n", check, [], [])
    named = "From: =?utf-8?q?J=C3=BCrgen_M=C3=BCller?= <j@x.example>\r\nSubject: hi\r\n"
    assert _matches(named, check, [], [])
    moved = 'From: "A Very Long Display Name Indeed"\r\n <d@fau.de>\r\nSubject: ARDS\r\n'
    assert _matches(moved, {}, ["d@fau.de"], [])
    assert not _matches(moved, {"subject_any": ["ARDS"], "from_domains_not": ["fau.de"]}, [], [])


def test_one_working_check_is_enough_and_every_check_must_agree_to_skip(tmp_path, routine):
    checks = [{"kind": "max_quiet", "days": 5}, {"kind": "state", "file": "state/q"}]
    assert kit.evaluate(ctx(tmp_path, checks, last_ok=ok_since(1)))["decision"] == "skip"
    (routine / "state/q").write_text("item")
    out = kit.evaluate(ctx(tmp_path, checks, last_ok=ok_since(1)))
    assert out["decision"] == "run" and "state" in out["reason"]


def test_a_broken_context_runs_rather_than_guessing(capsys):
    assert kit.main(["gatekit", "{not json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "run"


def test_the_config_vocabulary_is_the_kit_vocabulary():
    """RunGateConfig validates against the kit's own table, so the console, the loader and the
    runner cannot disagree on what a check is."""
    for kind, (_meaning, spec) in gatekit.KINDS.items():
        example = {"kind": kind, **{n: {"list": ["x"], "int": 3, "bool": True}.get(t, "x")
                                    for n, (t, req, _h) in spec.items() if req}}
        if kind == "mail":
            example.update(user_secret="U", password_secret="P")
        if kind == "dates":
            example["from"] = "2026-01-01"
        if kind == "unpaired_files":
            example["output"] = "{stem}.out"
        if kind == "weekdays":
            example["days"] = [0]
        RunGateConfig(enabled=True, checks=[example])


# ------------------------------------------------------------------ the baseline (daemon side)


def status(run_dir: Path, **fields):
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json", fields)


def test_last_ok_passes_over_skips_and_reads_fingerprints(tmp_path):
    runs = tmp_path / "runs"
    status(runs / "20260928-080000", run_id="me:20260928-080000", state="finished",
           outcome="ok")
    atomic_write_json(runs / "20260928-080000" / "gate.json", {"fingerprints": {"a": "f1"}})
    status(runs / "20260929-080000", run_id="me:20260929-080000", state="finished",
           outcome="skipped")
    base, why = gate_prepare.last_ok(tmp_path, "me:20260929-090000")
    assert why == "" and base["fingerprints"] == {"a": "f1"}
    assert base["started"].startswith("2026-09-28T08:00:00")


@pytest.mark.parametrize("outcome", ["partial", "failed"])
def test_a_run_that_did_not_finish_ok_means_run_again(tmp_path, outcome):
    status(tmp_path / "runs" / "20260928-080000", run_id="me:20260928-080000",
           state="finished" if outcome == "partial" else "failed", outcome=outcome)
    base, why = gate_prepare.last_ok(tmp_path, "me:20260929-090000")
    assert base is None and "left work behind" in why


def test_the_current_run_is_not_its_own_baseline(tmp_path):
    (tmp_path / "runs" / "20260929-090000").mkdir(parents=True)   # no status yet
    base, why = gate_prepare.last_ok(tmp_path, "me:20260929-090000")
    assert (base, why) == (None, "")


def test_a_phase_is_work_unless_it_is_a_waiting_value(tmp_path, routine):
    atomic_write_json(routine / "state/phase.json", {"phase": "wind-down"})
    check = [{"kind": "state", "file": "state/phase.json", "key": "phase",
              "idle_values": ["wind-down", "frozen"]}]
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "skip"
    atomic_write_json(routine / "state/phase.json", {"phase": "steady"})
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "run"
    (routine / "state/phase.json").unlink()
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "run"      # unknown phase = work


def test_an_item_lacking_its_verdict_is_work(tmp_path, routine):
    atomic_write_json(routine / "state/models.json", [{"id": "a", "verdict": "usable"}])
    check = [{"kind": "state", "file": "state/models.json", "key": "",
              "missing_field": "verdict"}]
    check[0]["key"] = "*"
    out = kit.evaluate(ctx(tmp_path, [{"kind": "state", "file": "state/models.json",
                                         "key": "items", "missing_field": "verdict"}]))
    assert out["decision"] == "run"                                  # no `items` key: unknown
    atomic_write_json(routine / "state/models.json", {"items": [{"id": "a", "verdict": "ok"}]})
    same = [{"kind": "state", "file": "state/models.json", "key": "items",
             "missing_field": "verdict"}]
    assert kit.evaluate(ctx(tmp_path, same))["decision"] == "skip"
    atomic_write_json(routine / "state/models.json", {"items": [{"id": "a"}]})
    assert kit.evaluate(ctx(tmp_path, same))["decision"] == "run"


def test_date_windows_and_run_kept_dates(tmp_path, routine):
    window = [{"kind": "dates", "from": "2026-09-20", "until": "2026-09-30"}]
    assert kit.evaluate(ctx(tmp_path, window))["decision"] == "run"
    later = [{"kind": "dates", "from": "2026-10-17", "until": "2026-10-31"}]
    assert kit.evaluate(ctx(tmp_path, later))["decision"] == "skip"
    atomic_write_json(routine / "state/due.json", {"due": ["2026-10-02", "2026-12-01"]})
    kept = [{"kind": "dates", "file": "state/due.json", "key": "due", "within_days": 3}]
    assert kit.evaluate(ctx(tmp_path, kept))["decision"] == "run"      # 2 Oct is 3 days out
    kept[0]["within_days"] = 1
    assert kit.evaluate(ctx(tmp_path, kept))["decision"] == "skip"


def test_sources_without_outputs_are_a_queue(tmp_path, routine):
    folder = tmp_path / "videos"
    folder.mkdir()
    (folder / "a.mp4").write_text("x")
    (folder / "a [done].mp4").write_text("x")
    check = [{"kind": "unpaired_files", "path": str(folder), "match": "*.mp4",
              "output": "{stem} [done]{suffix}", "exclude": ["tmp*"]}]
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "skip"
    (folder / "b.mp4").write_text("x")
    out = kit.evaluate(ctx(tmp_path, check))
    assert out["decision"] == "run" and "b.mp4" in out["reason"]


def test_json_paths_map_over_lists(tmp_path, routine):
    api = tmp_path / "models.json"
    api.write_text(json.dumps({"data": [{"id": "m1", "price": 1}, {"id": "m2", "price": 2}]}))
    check = [{"kind": "url_changed", "url": api.as_uri(), "select": "json:data.*.id",
              "id": "ids"}]
    fp = one(kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1))))["fingerprint"]
    api.write_text(json.dumps({"data": [{"id": "m1", "price": 9}, {"id": "m2", "price": 2}]}))
    assert kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1, {"ids": fp})))[
        "decision"] == "skip"                            # prices moved, the id set did not
    api.write_text(json.dumps({"data": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}))
    assert kit.evaluate(ctx(tmp_path, check, last_ok=ok_since(1, {"ids": fp})))[
        "decision"] == "run"


def test_hub_feedback_without_its_login_is_work(tmp_path, routine, monkeypatch):
    monkeypatch.delenv("WEB_AUTH_SOURCES", raising=False)
    out = kit.evaluate(ctx(tmp_path, [{"kind": "hub_feedback", "project": "ards"}]))
    assert out["decision"] == "run" and "WEB_AUTH_SOURCES" in one(out)["reason"]


def test_the_hub_login_secret_is_what_the_daemon_injects():
    assert gatekit.secrets_named([{"kind": "hub_feedback", "project": "p"}]) == {
        "WEB_AUTH_SOURCES"}
    assert gatekit.needs_net([{"kind": "hub_feedback", "project": "p"}])


def test_a_mail_watch_list_counts_any_of_its_entries():
    """A reply from a new address that names the project is still work: the positive filters
    are one watch list; only the excluded domains take a message away."""
    from rsched.gatekit.kit_net import _matches

    check = {"from_any": ["bertermann"], "subject_any": ["ARDS"],
             "from_domains_not": ["fau.de"]}

    def hdr(sender, subject):
        return f"From: {sender}\r\nSubject: {subject}\r\n"

    assert _matches(hdr("David Bertermann <d@geo.example>", "hello"), check, [], [])
    assert _matches(hdr("Someone New <x@new.example>", "Re: ARDS Abschluss"), check, [], [])
    assert not _matches(hdr("Someone <x@new.example>", "hello"), check, [], [])
    assert not _matches(hdr("Bertermann <b@fau.de>", "ARDS"), check, [], [])   # excluded
    assert _matches(hdr("Anyone <a@b.example>", "x"), {}, [], [])      # no list: every one
    assert not _matches(hdr("Circular <c@rrze.fau.de>", "x"),
                        {"from_domains_not": ["fau.de"]}, [], [])


def test_a_whole_file_list_lacking_a_field_is_work(tmp_path, routine):
    atomic_write_json(routine / "state/models.json", [{"id": "a", "verdict": "usable"}])
    check = [{"kind": "state", "file": "state/models.json", "missing_field": "verdict"}]
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "skip"
    atomic_write_json(routine / "state/models.json", [{"id": "a", "verdict": "ok"}, {"id": "b"}])
    out = kit.evaluate(ctx(tmp_path, check))
    assert out["decision"] == "run" and "1 item(s)" in out["reason"]


def test_a_finished_items_date_is_never_due(tmp_path, routine):
    atomic_write_json(routine / "state/obligations.json", {"obligations": [
        {"id": "a", "due_date": "2026-08-01", "status": "sent"},
        {"id": "b", "due_date": "2026-10-20", "status": "open"},
        {"id": "c", "due_date": None, "status": "needs-user-input"}]})
    check = [{"kind": "dates", "file": "state/obligations.json", "key": "obligations.*.due_date",
              "done_key": "status", "done_values": ["sent", "done"], "within_days": 4}]
    assert gatekit.validate(check) == []
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "skip"   # a is done, b is far
    check[0]["done_values"] = ["done"]
    assert kit.evaluate(ctx(tmp_path, check))["decision"] == "run"    # a is overdue again
    bad = [{"kind": "dates", "file": "state/x.json", "key": "due", "done_key": "status"}]
    assert gatekit.validate(bad)


def test_a_negative_lead_time_is_refused():
    """`within_days` counts a date EARLY; a negative one counts it late — a duty due today
    read as not due for days, the one wrong answer a gate must not give."""
    bad = [{"kind": "dates", "file": "state/d.json", "key": "due", "within_days": -2}]
    assert any("within_days" in p for p in gatekit.validate(bad))
    assert gatekit.validate([{**bad[0], "within_days": 0}]) == []


def test_done_items_that_are_not_records_are_work(tmp_path, routine):
    """With `done_key` each listed item is a record holding its date and its status; a list of
    bare dates there cannot be read as configured — work, never "no dated duty is due"."""
    atomic_write_json(routine / "state/d.json", {"items": ["2026-09-01"]})
    check = [{"kind": "dates", "file": "state/d.json", "key": "items.*.due",
              "done_key": "status"}]
    out = kit.evaluate(ctx(tmp_path, check))
    assert out["decision"] == "run" and "could not check" in one(out)["reason"]


def test_a_special_use_folder_is_found_whatever_the_server_calls_it():
    from rsched.gatekit.kit_net import (  # the kit's own class, as it raises it
        UnknownError,
        _resolve,
    )

    class Conn:
        def list(self):
            return "OK", [b'(\\HasNoChildren) "/" "INBOX"',
                          b'(\\All \\HasNoChildren) "/" "[Google Mail]/Alle Nachrichten"',
                          b'(\\HasNoChildren \\Sent) "/" "[Google Mail]/Gesendet"']

    assert _resolve(Conn(), "\\All") == "[Google Mail]/Alle Nachrichten"
    assert _resolve(Conn(), "\\Sent") == "[Google Mail]/Gesendet"
    assert _resolve(Conn(), "INBOX") == "INBOX"
    with pytest.raises(UnknownError):
        _resolve(Conn(), "\\Junk")
