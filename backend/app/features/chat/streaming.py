"""
Streamed Replies: SSE Framing

Formats the server-sent events (SSE) a streamed chat reply is delivered in. Splitting the reply into speakable chunks is done by ai/tts/normalizer.py::speech_stream (see app/features/chat/pipeline.py::stream_turn).

How to use:
    from app.features.chat.streaming import sse_stream

    StreamingResponse(sse_stream(events), media_type="text/event-stream")
"""

import json
import queue
import threading
from collections.abc import Iterable, Iterator

# A reasoning model can be silent for a long time before its first word. Proxies in front of the
# app (an institution's reverse proxy, a load balancer) tend to close a connection that carried
# nothing for about a minute, so a comment frame goes out well before that.
KEEPALIVE_SECONDS = 15.0


def sse_event(event: str, data: dict) -> str:
    """Format one server-sent-event (SSE) frame — an "event: <name>\\ndata: <json>\\n\\n" block."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def sse_stream(events: Iterable[tuple[str, dict]], *, keepalive_seconds: float = KEEPALIVE_SECONDS) -> Iterator[str]:
    """SSE frames for `events`, with a comment frame (": keep-alive", ignored by every client)
    whenever nothing arrived for `keepalive_seconds`.

    `events` runs in a helper thread so this side can wake up while it is blocked, e.g. on a model
    that is still thinking. If the client goes away, the helper stops after the event it is on.
    """
    items: queue.Queue = queue.Queue()
    stop = threading.Event()

    def produce() -> None:
        try:
            for item in events:
                if stop.is_set():
                    break
                items.put(("event", item))
            items.put(("end", None))
        except BaseException as exc:  # handed to the consumer, which re-raises it
            items.put(("error", exc))
        finally:
            close = getattr(events, "close", None)
            if close is not None:
                close()

    threading.Thread(target=produce, daemon=True).start()
    try:
        while True:
            try:
                kind, value = items.get(timeout=keepalive_seconds)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if kind == "end":
                return
            if kind == "error":
                raise value
            yield sse_event(*value)
    finally:
        stop.set()
