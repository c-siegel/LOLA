"""
Analytics Queries

The actual database queries behind the analytics routes (features/analytics/stats_router.py
and conversations_router.py): aggregate stats, a paginated session list, a timeseries for
charts, and the conversations selected for export/deletion — each scoped to a user's own
projects and optionally filtered by project/model/time period. The CSV/ZIP rendering lives in
csv_export.py.

How to use:
    from app.features.analytics.service import get_stats

    stats = get_stats(session, user_id, days=7)
"""

import json
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, func, select

from app.core.ownership import find_owned
from app.features.analytics.schemas import (
    AnalyticsStatsOut,
    ConversationDetailOut,
    SessionRowOut,
    TranscriptCheckOut,
    TranscriptMessageOut,
    TranscriptMotionOut,
    TranscriptSourceOut,
)
from app.features.chat.models import Conversation, ProjectAccess
from app.features.knowledge.service import document_names
from app.features.projects.models import Project
from app.features.projects.schemas import ProjectStats
from app.features.projects.service import list_projects

MAX_STATS_DAYS = 90
MAX_TIMESERIES_DAYS = 365


def get_conversation_detail(session: Session, user_id: str, conversation_id: str) -> ConversationDetailOut | None:
    """Full message-by-message transcript of one saved conversation — None if it doesn't exist
    or doesn't belong to one of user_id's own projects (the session-list table only ever shows a
    teacher their own projects' sessions, so this route needs the same check)."""
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    project = find_owned(session, Project, conversation.project_id, user_id)
    if project is None:
        return None
    messages = json.loads(conversation.messages_json)
    names = document_names(session, [s["documentId"] for m in messages for s in m.get("sources") or []])
    steps = {item["id"]: (number, item["title"]) for number, item in enumerate(project.checklist_items, start=1)}
    return ConversationDetailOut(
        id=conversation.id,
        project_title=project.title,
        visitor_name=conversation.visitor_name,
        started_at=conversation.started_at.isoformat(),
        messages=[
            TranscriptMessageOut(
                role=m["role"],
                content=m["content"],
                sources=[
                    TranscriptSourceOut(document_id=s["documentId"], filename=names.get(s["documentId"]), page=s.get("page"))
                    for s in m["sources"]
                ]
                if m.get("sources") is not None
                else None,
                motions=[TranscriptMotionOut(name=x["name"], offset=x["offset"]) for x in m["motions"]]
                if m.get("motions")
                else None,
                checks=[
                    TranscriptCheckOut(
                        id=c["id"],
                        done=c["done"],
                        offset=c["offset"],
                        number=steps[c["id"]][0] if c["id"] in steps else None,
                        title=steps[c["id"]][1] if c["id"] in steps else None,
                    )
                    for c in m["checks"]
                ]
                if m.get("checks")
                else None,
            )
            for m in messages
        ],
    )


def get_conversations_for_export(
    session: Session, user_id: str, conversation_ids: list[str]
) -> list[tuple[Conversation, Project]]:
    """Saved conversations (with their project) from `conversation_ids` that actually belong to
    one of user_id's own projects — same ownership check as get_conversation_detail, but as a
    single bulk query for the CSV/ZIP export. Any id that doesn't exist or belongs to someone
    else is silently dropped rather than erroring out the whole download."""
    if not conversation_ids:
        return []
    rows = session.exec(
        select(Conversation, Project)
        .join(Project, Project.id == Conversation.project_id)
        .where(Conversation.id.in_(conversation_ids), Project.user_id == user_id)
    ).all()
    return list(rows)


def delete_conversations(session: Session, user_id: str, conversation_ids: list[str]) -> int:
    """Permanently delete the given saved conversations, scoped to user_id's own projects (same
    ownership check as get_conversations_for_export) — returns how many were actually deleted.
    Ids that don't exist or belong to someone else are silently skipped rather than erroring,
    same reasoning as the export: a stale selection shouldn't block the rest of it."""
    rows = get_conversations_for_export(session, user_id, conversation_ids)
    for conversation, _project in rows:
        session.delete(conversation)
    session.commit()
    return len(rows)


def _apply_common_filters(query, user_id: str, project_id: str | None, model: str | None):
    """Apply the shared user/project/model filters used by every analytics query below."""
    query = query.where(Project.user_id == user_id)
    if project_id:
        query = query.where(Project.id == project_id)
    if model:
        query = query.where(Project.llm_model == model)
    return query


def get_stats(
    session: Session,
    user_id: str,
    project_id: str | None = None,
    days: int = 7,
    model: str | None = None,
) -> AnalyticsStatsOut:
    """Calculate aggregate analytics stats for a user (optionally filtered by project/model)."""
    days = min(days, MAX_STATS_DAYS)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Sessions = page loads of the published chat page (ProjectAccess), as a plain COUNT instead
    # of loading the rows — previously every access row re-summed all of the project's
    # conversations, which would multiply messages/avg-duration for repeat visits to one project.
    access_count_query = _apply_common_filters(
        select(func.count(ProjectAccess.id)).join(Project, Project.id == ProjectAccess.project_id),
        user_id,
        project_id,
        model,
    ).where(ProjectAccess.accessed_at >= cutoff)
    sessions_count = session.exec(access_count_query).one()

    # Messages/duration come from the conversations updated within the period — once per
    # conversation, not once per page load.
    conversations_query = _apply_common_filters(
        select(Conversation).join(Project, Project.id == Conversation.project_id),
        user_id,
        project_id,
        model,
    ).where(Conversation.updated_at >= cutoff)
    conversations = session.exec(conversations_query).all()

    messages_count = 0
    total_duration_seconds = 0.0
    for conv in conversations:
        msg_list = json.loads(conv.messages_json)
        messages_count += len(msg_list)
        total_duration_seconds += (conv.updated_at - conv.started_at).total_seconds()
    avg_duration = int(total_duration_seconds / len(conversations)) if conversations else 0

    # Deltas (comparison to the previous period) and token cost are deliberately not part of this
    # phase — that would need real per-litellm-call token/cost tracking, which isn't persisted
    # anywhere yet. Returned as 0 instead of faking a number.
    return AnalyticsStatsOut(
        sessions=sessions_count,
        sessions_delta_pct=0,
        messages=messages_count,
        messages_delta_pct=0,
        avg_duration_seconds=avg_duration,
        avg_duration_delta_pct=0,
        token_cost_eur=0,
        token_cost_delta_pct=0,
    )


def get_sessions_paginated(
    session: Session,
    user_id: str,
    project_id: str | None = None,
    model: str | None = None,
    days: int | None = None,
    page: int = 1,
    per_page: int = 10,
) -> dict:
    """Return a paginated page of conversations (one row per session in the analytics table)."""
    offset = (page - 1) * per_page

    # Selects Project.title alongside Conversation directly, instead of a separate session.get()
    # per row below — the join is already here for the WHERE filters, so this is the same query,
    # not an extra one, and it scales with the page instead of a full N+1 if per_page ever grows.
    query = _apply_common_filters(
        select(Conversation, Project.title).join(Project, Project.id == Conversation.project_id),
        user_id,
        project_id,
        model,
    )
    count_query = _apply_common_filters(
        select(func.count(Conversation.id)).join(Project, Project.id == Conversation.project_id),
        user_id,
        project_id,
        model,
    )

    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=min(days, MAX_STATS_DAYS))
        query = query.where(Conversation.started_at >= cutoff)
        count_query = count_query.where(Conversation.started_at >= cutoff)

    query = query.order_by(Conversation.started_at.desc()).offset(offset).limit(per_page)
    rows = session.exec(query).all()
    total = session.exec(count_query).one()

    items = []
    for conv, project_title in rows:
        msg_list = json.loads(conv.messages_json)
        duration = int((conv.updated_at - conv.started_at).total_seconds())

        last_question = None
        for msg in reversed(msg_list):
            if msg.get("role") == "user":
                last_question = msg.get("content", "")
                break

        items.append(
            SessionRowOut(
                id=conv.id,
                project_title=project_title or "Unbekannt",
                started_at=conv.started_at.isoformat(),
                message_count=len(msg_list),
                duration_seconds=duration,
                last_question=last_question[:100] if last_question else None,
                visitor_name=conv.visitor_name,
            )
        )

    return {"items": items, "total": total}


def get_session_ids(
    session: Session,
    user_id: str,
    project_id: str | None = None,
    model: str | None = None,
    days: int | None = None,
) -> list[str]:
    """Every conversation id matching the given filters, ignoring pagination — powers the
    analytics table's "select all" button, which needs to select matches beyond the currently
    loaded page (see get_sessions_paginated for the same filters applied to a page)."""
    query = _apply_common_filters(
        select(Conversation.id).join(Project, Project.id == Conversation.project_id),
        user_id,
        project_id,
        model,
    )
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=min(days, MAX_STATS_DAYS))
        query = query.where(Conversation.started_at >= cutoff)
    return list(session.exec(query.order_by(Conversation.started_at.desc())))


def get_timeseries_data(
    session: Session,
    user_id: str,
    project_id: str | None = None,
    model: str | None = None,
    days: int = 30,
    granularity: str = "day",
) -> list[dict]:
    """Return timeseries data (sessions per day/week/month) for the analytics chart."""
    days = min(days, MAX_TIMESERIES_DAYS)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = _apply_common_filters(
        select(ProjectAccess.accessed_at).join(Project, Project.id == ProjectAccess.project_id),
        user_id,
        project_id,
        model,
    ).where(ProjectAccess.accessed_at >= cutoff)
    accesses = session.exec(query).all()

    timeseries: dict[str, int] = {}
    for accessed_at in accesses:
        if granularity == "week":
            key = accessed_at.strftime("%Y-%W")
        elif granularity == "month":
            key = accessed_at.strftime("%Y-%m")
        else:
            key = accessed_at.strftime("%Y-%m-%d")
        timeseries[key] = timeseries.get(key, 0) + 1

    return [{"label": k, "value": v} for k, v in sorted(timeseries.items())]


def get_project_overview(session: Session, user_id: str) -> ProjectStats:
    """Summary stats for the dashboard overview: project count, published count, and
    sessions/messages this week."""
    projects = list_projects(session, user_id)
    weekly = get_stats(session, user_id, days=7)
    return ProjectStats(
        total_projects=len(projects),
        published_projects=sum(1 for p in projects if p.published),
        sessions_last_7_days=weekly.sessions,
        messages_last_7_days=weekly.messages,
    )
