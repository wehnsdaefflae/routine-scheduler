"""read_file's WINDOW — which lines of a stream one read returns, and how it is reported.

Beyond a plain slice:

- a NEGATIVE `start_line` counts from the END — `-40` is the last 40 lines. The stream is still
  read once, keeping only the tail in a bounded deque, and the observation reports the resolved
  line numbers.
- a `max_lines` over `MAX_LINES` is served at the cap and the observation SAYS so
  (`max_lines_capped`) rather than refusing the whole read.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from .observations import OBS_CAP_CHARS

if TYPE_CHECKING:
    from collections.abc import Iterable

DEFAULT_LINES = 200
MAX_LINES = 500


def cap(asked: int) -> int:
    """The lines one read returns for a `max_lines` of `asked` (0 = not given)."""
    return min(asked or DEFAULT_LINES, MAX_LINES)


def window(lines: Iterable[str], start: int, asked: int) -> tuple[list[str], int, int]:
    """The window of a line STREAM — `(lines, the stream's length, the first line's number)`.

    `start` ≥ 1 is the first line; a negative one counts from the end. The stream is consumed
    to its end for the count but never held whole — one line in memory at a time (a tail read
    holds at most `-start` of them), and the file is already under READ_MAX_BYTES, so the count
    is cheap.
    """
    total, size = 0, cap(asked)
    if start < 0:
        tail: deque[str] = deque(maxlen=-start)
        for line in lines:
            tail.append(line)
            total += 1
        return list(tail)[:size], total, total - len(tail) + 1
    window_: list[str] = []
    for total, line in enumerate(lines, 1):
        if start <= total < start + size:
            window_.append(line)
    return window_, total, start


def windowed(rel_path: str, window: list[str], total: int, start: int,
             asked: int, start_char: int = 0) -> dict:
    """The window as its observation: whole lines, END-truncated at the cap, with the exact
    `start_line` (and, inside a line longer than the cap, `start_char`) that continues it.
    `start` is the window's first line as RESOLVED (a tail read's is counted from the end);
    `start_char` is where that line was entered — `window[0]` already starts there. A
    `max_lines` over the bar is said (`max_lines_capped`), never quietly served short.
    """
    end_line = min(start - 1 + cap(asked), total)
    content = "\n".join(window)
    truncated = False
    if len(content) > OBS_CAP_CHARS:
        # END-truncate a file read: keep whole lines from the HEAD and report the exact next
        # start_line, so a follow-up read continues IN SEQUENCE (a file read is ordered, unlike
        # opaque output's head+tail elision).
        kept: list[str] = []
        used = 0
        for ln in window:
            if kept and used + len(ln) + 1 > OBS_CAP_CHARS:
                break
            kept.append(ln)
            used += len(ln) + 1
        end_line = start + len(kept) - 1
        core = "\n".join(kept)
        resume = f"start_line={end_line + 1}"
        if len(core) > OBS_CAP_CHARS:
            # A single line longer than the cap: the rest of it is the next page (resuming at
            # the next LINE would skip it, e.g. a one-line minified JSON spill).
            core = core[:OBS_CAP_CHARS]
            resume = f"start_line={start} start_char={start_char + OBS_CAP_CHARS}"
        content = (core + f"\n[... {len(kept)} of {len(window)} window lines shown (through line "
                   f"{end_line} of {total}); truncated at {OBS_CAP_CHARS} chars — re-read "
                   f"with {resume} to continue in sequence ...]")
        truncated = True
    return {"path": rel_path, "start_line": start,
            **({"start_char": start_char} if start_char else {}),
            **({"max_lines_capped": asked} if asked > MAX_LINES else {}),
            "end_line": end_line, "total_lines": total,
            "content": content, "truncated": truncated}
