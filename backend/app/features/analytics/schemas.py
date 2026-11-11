"""
Analytics Request/Response Shapes

The response shapes for app/features/analytics/stats_router.py's and conversations_router.py's endpoints: aggregate stats, one row in the
paginated session list, and one point on the timeseries chart.

How to use:
    from app.features.analytics.schemas import AnalyticsStatsOut
"""

from app.core.schema import CamelModel
from app.features.chat.schemas import ChatHistoryEntry


class AnalyticsStatsOut(CamelModel):
    sessions: int
    sessions_delta_pct: float
    messages: int
    messages_delta_pct: float
    avg_duration_seconds: int
    avg_duration_delta_pct: float
    token_cost_eur: float
    token_cost_delta_pct: float
    # Replies of projects that route between a fast and a deep model, by route (see
    # features/chat/reasoning_router.py); both 0 when no project does.
    fast_replies: int = 0
    deep_replies: int = 0


class SessionRowOut(CamelModel):
    id: str
    project_title: str
    started_at: str
    message_count: int
    duration_seconds: int
    last_question: str | None
    # The name/ID the visitor typed in, if the project's teacher enabled that gate (see
    # features/projects/models.py::Project.require_visitor_name) — None for every session that didn't ask.
    visitor_name: str | None


class TranscriptSourceOut(CamelModel):
    """A knowledge-base passage an assistant message was given (see features/knowledge/)."""

    document_id: str
    # None once the document has been deleted — the transcript keeps the reference, not the text.
    filename: str | None
    page: int | None


class TranscriptMotionOut(CamelModel):
    """An avatar gesture the LLM marked in a reply (see features/chat/motion.py)."""

    name: str
    # Character position in the message content where the marker stood.
    offset: int


class TranscriptCheckOut(CamelModel):
    """A conversation-plan step the LLM ticked off or took back in a reply (see
    features/chat/checklist.py)."""

    id: str
    done: bool
    # Character position in the message content where the marker stood.
    offset: int
    # The step's current title and position in the plan; None once the step has been deleted.
    title: str | None
    number: int | None


class TranscriptMessageOut(ChatHistoryEntry):
    # Only on assistant messages of projects with a knowledge base; None otherwise.
    sources: list[TranscriptSourceOut] | None = None
    # Only on assistant messages in which the LLM used gestures; None otherwise.
    motions: list[TranscriptMotionOut] | None = None
    # Only on assistant messages in which the LLM ticked plan steps; None otherwise.
    checks: list[TranscriptCheckOut] | None = None
    # Only on assistant messages of projects with reasoning: "fast" or "deep", and the model that
    # answered.
    route: str | None = None
    model: str | None = None


class ConversationDetailOut(CamelModel):
    """Full message-by-message transcript of one saved conversation — what GET
    /conversations/{id} returns, as opposed to SessionRowOut's one-line summary."""

    id: str
    project_title: str
    visitor_name: str | None
    started_at: str
    messages: list[TranscriptMessageOut]


class SessionsPageOut(CamelModel):
    # {items, total} instead of a bare list — the frontend needs total for real pagination
    # ("1-4 of 1,284", see frontend/src/types/analytics.ts::SessionsPage).
    items: list[SessionRowOut]
    total: int


class TimeseriesPointOut(CamelModel):
    label: str
    value: int


class ConversationIdsIn(CamelModel):
    """Body shared by POST /conversations/export and POST /conversations/batch-delete — which saved
    conversations (checked off in the analytics table, see
    frontend/src/pages/Dashboard/Analytics) to bundle into a CSV/ZIP download or delete."""

    conversation_ids: list[str]
