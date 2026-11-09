"""
Local Transcription Capacity

Limits how many local (Parakeet) transcriptions run at the same time in this
process. Each one is CPU-bound and already uses most of the machine's cores, so running
several at once wouldn't finish any sooner — it would just make every one slower.

How to use:
    from app.features.ai.stt.capacity import transcription_slot

    with transcription_slot():
        ...  # run the model
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.config import settings

# A BoundedSemaphore (not a plain lock) so a size > 1 is possible without code changes here.
_slots = threading.BoundedSemaphore(max(1, settings.stt_max_concurrent_transcriptions))


@contextmanager
def transcription_slot() -> Iterator[None]:
    """Hold one of the stt_max_concurrent_transcriptions slots while the block runs.

    Raises TimeoutError if none frees up within stt_transcription_queue_timeout_seconds —
    callers should treat that like any other transcription failure (a 503, not a 500), since
    it means the server is at capacity, not broken.
    """
    if not _slots.acquire(timeout=settings.stt_transcription_queue_timeout_seconds):
        raise TimeoutError("Transcription queue is full — no free slot within the timeout.")
    try:
        yield
    finally:
        _slots.release()
