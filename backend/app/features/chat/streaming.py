"""
Streamed Replies: SSE Framing

Formats the server-sent events (SSE) a streamed chat reply is delivered in. Splitting the reply into speakable chunks is done by ai/tts/normalizer.py::speech_stream (see app/features/chat/pipeline.py::stream_turn).

How to use:
    from app.features.chat.streaming import sse_event

    StreamingResponse((sse_event(event, data) for event, data in events), media_type="text/event-stream")
"""

import json


def sse_event(event: str, data: dict) -> str:
    """Format one server-sent-event (SSE) frame — an "event: <name>\\ndata: <json>\\n\\n" block."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
