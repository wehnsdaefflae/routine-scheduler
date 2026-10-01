"""Secrets store round-trips — including multi-line values (SSH private keys)."""

from rsched import secrets

PEM = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
       "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB\n"
       "AAAAMwAAAAtzc2gtZWQyNTUx\n"
       "-----END OPENSSH PRIVATE KEY-----\n")


def _patch_store(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "secrets_path", lambda: tmp_path / "secrets.env")


def test_single_line_values_round_trip_in_historical_format(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    secrets.set_secret("API_KEY", "sk-plain-123")
    secrets.set_secret("OTHER", "v2")
    raw = (tmp_path / "secrets.env").read_text(encoding="utf-8")
    assert "API_KEY=sk-plain-123\n" in raw          # byte-identical historical format
    assert secrets.load_secrets() == {"API_KEY": "sk-plain-123", "OTHER": "v2"}


def test_multiline_pem_round_trips(monkeypatch, tmp_path):
    """A pasted SSH private key (the remote-machines key_var flow) must survive the store:
    the old line-based writer silently corrupted it into stray pseudo-keys."""
    _patch_store(monkeypatch, tmp_path)
    secrets.set_secret("GPU_BOX_SSH_KEYS", PEM)
    secrets.set_secret("PLAIN", "x")
    raw = (tmp_path / "secrets.env").read_text(encoding="utf-8")
    assert len(raw.splitlines()) == 2               # one line per secret, quoted PEM
    got = secrets.load_secrets()
    assert got["GPU_BOX_SSH_KEYS"] == PEM
    assert got["PLAIN"] == "x"
    # fragments must never resurface as bogus key names
    assert all(k in ("GPU_BOX_SSH_KEYS", "PLAIN") for k in got)
    # a later unrelated write keeps the PEM intact
    secrets.set_secret("PLAIN", "y")
    assert secrets.load_secrets()["GPU_BOX_SSH_KEYS"] == PEM


def test_legacy_quoted_single_line_values_still_parse(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    (tmp_path / "secrets.env").write_text(
        'A="quoted"\nB=\'single\'\n# comment\nC=plain\n', encoding="utf-8")
    assert secrets.load_secrets() == {"A": "quoted", "B": "single", "C": "plain"}


def test_concurrent_edits_of_one_store_all_land(monkeypatch, tmp_path):
    """Every setter is a read-modify-write of the whole file, and the settings handlers run on
    worker threads: six entries saved into one JSON-map secret at once each read the same map
    and the last write kept only its own. Under the store's lock every one lands."""
    import json

    from conftest import hammer

    _patch_store(monkeypatch, tmp_path)

    def add_entry(tag: int) -> None:
        secrets.update_secret("FTP_SOURCES", lambda raw: json.dumps(
            {**json.loads(raw or "{}"), f"src{tag}": {"host": f"h{tag}"}}))
        secrets.set_secret(f"KEY_{tag}", str(tag))

    assert hammer(add_entry) == []
    store = secrets.load_secrets()
    assert sorted(json.loads(store["FTP_SOURCES"])) == [f"src{n}" for n in range(6)]
    assert all(store[f"KEY_{n}"] == str(n) for n in range(6))
    assert (tmp_path / "secrets.env").stat().st_mode & 0o777 == 0o600
