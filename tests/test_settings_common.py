"""The settings package's one config.yaml writer (web/settings/common.update_config)."""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import yaml

from rsched.web.settings.common import update_config


def test_concurrent_saves_never_lose_an_edit(tmp_path):
    """Every settings handler is sync, so two saves run on two worker threads at once; each
    read config.yaml, changed its own key and wrote its copy back, and the later write undid
    the earlier save. Hammered like this, 46 of 240 edits survived the unlocked writer."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"token": "t"}), encoding="utf-8")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        server=SimpleNamespace(source=cfg))))

    def save(tag: int) -> None:
        for i in range(25):
            update_config(request, lambda raw, k=f"k{tag}_{i}": raw.update({k: 1}))

    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        pool = [threading.Thread(target=save, args=(n,)) for n in range(6)]
        for t in pool:
            t.start()
        for t in pool:
            t.join()
    finally:
        sys.setswitchinterval(interval)
    saved = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert len(saved) == 1 + 6 * 25 and saved["token"] == "t"
