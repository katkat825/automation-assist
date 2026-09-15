"""Cooperative cancellation for the runtime QA pipeline.

The QA Companion (``--observe``) and the driven runner both do slow work
*before* a browser window ever appears — unzip, transcode media, launch the
page. If those hang (a giant video, a course that never mounts a slide) the UI
just sits there "loading". To let the reviewer bail out, the pipeline threads a
single ``cancel`` object (anything with ``is_set()`` — a ``threading.Event``
fits) through those stages and calls :func:`check` at safe boundaries.

Cancellation is cooperative: stages only stop at the checkpoints, so a long
external call (ffmpeg) is run so it can itself be interrupted. When cancel is
requested, :func:`check` raises :class:`Cancelled`, which the top-level runner
catches to tidy up (close the server/browser, remove temp files) and report a
clean "cancelled" instead of a crash.

Passing ``cancel=None`` disables all of this — that is the default everywhere,
so the command-line entry points behave exactly as before.
"""
from __future__ import annotations

from typing import Optional, Protocol


class _Cancellable(Protocol):
    def is_set(self) -> bool: ...


class Cancelled(Exception):
    """Raised at a checkpoint when cancellation has been requested."""


def is_cancelled(cancel: Optional[_Cancellable]) -> bool:
    """True if a cancel signal was passed and is set. Safe with None."""
    return cancel is not None and cancel.is_set()


def check(cancel: Optional[_Cancellable]) -> None:
    """Raise :class:`Cancelled` if cancellation has been requested; else no-op.

    Call at safe points (between videos, inside wait loops) so long stages can
    stop promptly without leaving a half-open browser or server behind.
    """
    if is_cancelled(cancel):
        raise Cancelled()
