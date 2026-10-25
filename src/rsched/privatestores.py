"""Every path some util's `fs:` line claims as a PRIVATE store, across the whole library.

A question about the LIBRARY's declarations, not about one run's policy, which is why it lives
beside `sandbox` rather than inside it: the answer is the same for every run on the instance and
is cached on the library's own mtime. `sandbox.wrap` subtracts these paths from the wholesale
`roots` mount, so a grant that exists to let ONE util reach its credential store does not hand
that store to every other util in the run; the declarer gets it back by name (`sandbox._admit`).

Split out of `sandbox.py` when D207 needed room there for the run-history depth: the sandbox
module is over the house line budget and a file over the bar may only shrink
(tests/test_file_size.py).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from .paths import expand


@lru_cache(maxsize=8)
def _cached(libraries_home: str, stamp: float) -> frozenset[Path]:  # noqa: ARG001
    # `stamp` is the CACHE KEY, not an input: it is the newest util-dir mtime, so a
    # write_util revision produces a different key and the scan reruns.
    from .utils_lib import list_utils
    out: set[Path] = set()
    try:
        for util in list_utils(Path(libraries_home)):
            for _mode, declared in util.get("fs_paths") or ():
                expanded = os.path.expandvars(declared)
                if "$" not in expanded:      # an unset variable names no path to subtract
                    out.add(expand(expanded))
    except OSError:
        return frozenset()
    return frozenset(out)


def paths(libraries_home: Path) -> frozenset[Path]:
    """Every privately-claimed store path in the library at `libraries_home`.

    Keyed on the newest util directory mtime, so a `write_util` revision (an atomic
    tmp+rename inside that directory) invalidates the cache without needing a restart.
    """
    root = Path(libraries_home) / "utils"
    try:
        stamp = max((d.stat().st_mtime for d in root.iterdir() if d.is_dir()), default=0.0)
    except OSError:
        return frozenset()
    return _cached(str(libraries_home), stamp)
