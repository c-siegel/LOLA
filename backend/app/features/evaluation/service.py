"""
Evaluation: Test Sets and Runs

The logic behind the evaluation routes (features/evaluation/router.py). A test set belongs to a
knowledge base and holds questions with optional reference answers, written by hand, imported from
CSV, or drafted by the judge LLM from the material (drafts need the teacher's approval). A run
answers a test set's approved questions through one of the teacher's projects and has the answers
scored by the evaluation service; runner.py does that in the background.

The judge is one of the teacher's own LLM keys, decrypted only to be sent along with each request
to the evaluation service. Arcana keys can't judge: Arcana adds its own retrieval to every call.

How to use:
    from app.features.evaluation import service

    test_set = service.create_test_set(session, user_id, kb, "Kapitel 1", "de")
    run = service.start_run(session, user_id, project, test_set, judge_key_id, ["faithfulness"])
"""

import csv
import io
import json
import logging
import statistics
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func
from sqlmodel import Session, select

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.core.providers import GWDG_ARCANA_PROVIDER, KEY_TYPE_LLM, build_model_string, provider_label
from app.features.analytics.csv_export import _csv_safe
from app.features.api_keys.crypto import reveal_api_key
from app.features.api_keys.resolve import effective_api_base, get_owned_key_of_type, resolve_llm_key
from app.features.evaluation import cleanup, eval_client
from app.features.evaluation.models import EvalRun, EvalRunItem, EvalTestCase, EvalTestSet
from app.features.knowledge import rag_client
from app.features.knowledge.models import KnowledgeBase
from app.features.knowledge.service import KnowledgeServiceUnavailable
from app.features.knowledge.sources import titles_by_document
from app.features.projects.models import Project
from app.features.site_settings.service import get_or_create_site_settings

logger = logging.getLogger(__name__)

METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "factual_correctness",
    "coverage",
    "restraint",
)
KINDS = ("grounded", "topic", "offtopic")
# Judge calls per question and metric with Ragas 0.4 (counted in rag-eval's tests); context
# precision makes one per retrieved passage, so it's multiplied by the project's top_k. Shown as
# the cost estimate before a run starts.
JUDGE_CALLS_PER_METRIC = {
    "faithfulness": 2,
    "answer_relevancy": 3,
    "context_precision": 1,
    "context_recall": 1,
    "factual_correctness": 4,
    # One call, and only for topic / offtopic questions respectively.
    "coverage": 1,
    "restraint": 1,
}
LANGUAGES = ("de", "en")
ACTIVE_STATUSES = ("queued", "answering", "scoring")

MAX_CASES_PER_TEST_SET = 500
MAX_QUESTION_CHARS = 4000
MAX_REFERENCE_CHARS = 8000
MAX_CSV_BYTES = 1024 * 1024
# Per "Draft questions" click: about ten judge calls, a few seconds each with four in parallel —
# short enough for one request. Teachers click again for more.
MAX_DRAFTS_PER_REQUEST = 10
_CHUNK_SAMPLE = 30
_MAX_NAME_LENGTH = 100

# The backend runs as a single process (one uvicorn worker), so in-memory guards are enough:
# one draft request per teacher at a time (each holds a request thread for up to a few minutes),
# and an atomic "no run active → create one".
_drafting_users: set[str] = set()
_drafting_lock = threading.Lock()
_run_start_lock = threading.Lock()


class EvaluationDisabled(DomainError):
    status_code = 404
    detail = ErrorCode.EVALUATION_DISABLED


class EvaluationServiceUnavailable(DomainError):
    status_code = 503
    detail = ErrorCode.EVALUATION_SERVICE_UNAVAILABLE


class EvaluationError(DomainError):
    status_code = 400


def require_enabled() -> None:
    if not settings.evaluation_enabled:
        raise EvaluationDisabled()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_name(name: str) -> str:
    name = (name or "").strip()[:_MAX_NAME_LENGTH]
    if not name:
        raise EvaluationError(ErrorCode.EVALUATION_NAME_REQUIRED)
    return name


def _clean_text(value: str | None, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


# --- Test sets -------------------------------------------------------------------------------


def list_test_sets(session: Session, kb: KnowledgeBase) -> list[EvalTestSet]:
    return list(
        session.exec(
            select(EvalTestSet).where(EvalTestSet.knowledge_base_id == kb.id).order_by(EvalTestSet.created_at)
        )
    )


def list_user_test_sets(session: Session, user_id: str) -> list[EvalTestSet]:
    return list(session.exec(select(EvalTestSet).where(EvalTestSet.user_id == user_id).order_by(EvalTestSet.created_at)))


def case_counts(session: Session, test_set_ids: list[str]) -> dict[str, tuple[int, int]]:
    """{test_set_id: (all questions, approved questions)} in one query."""
    if not test_set_ids:
        return {}
    rows = session.exec(
        select(EvalTestCase.test_set_id, EvalTestCase.approved, func.count(EvalTestCase.id))
        .where(EvalTestCase.test_set_id.in_(test_set_ids))
        .group_by(EvalTestCase.test_set_id, EvalTestCase.approved)
    ).all()
    counts: dict[str, tuple[int, int]] = {}
    for test_set_id, approved, count in rows:
        total, approved_count = counts.get(test_set_id, (0, 0))
        counts[test_set_id] = (total + count, approved_count + (count if approved else 0))
    return counts


def create_test_set(session: Session, user_id: str, kb: KnowledgeBase, name: str, language: str) -> EvalTestSet:
    test_set = EvalTestSet(
        user_id=user_id,
        knowledge_base_id=kb.id,
        name=_clean_name(name),
        language=language if language in LANGUAGES else "de",
    )
    session.add(test_set)
    session.commit()
    session.refresh(test_set)
    return test_set


def update_test_set(session: Session, test_set: EvalTestSet, name: str | None, language: str | None) -> EvalTestSet:
    if name is not None:
        test_set.name = _clean_name(name)
    if language in LANGUAGES:
        test_set.language = language
    session.add(test_set)
    session.commit()
    session.refresh(test_set)
    return test_set


def delete_test_set(session: Session, test_set: EvalTestSet) -> None:
    """With its questions and every run that used it."""
    cleanup.delete_test_sets(session, [test_set.id])
    session.commit()


# --- Test cases ------------------------------------------------------------------------------


def list_cases(session: Session, test_set: EvalTestSet) -> list[EvalTestCase]:
    return list(
        session.exec(
            select(EvalTestCase).where(EvalTestCase.test_set_id == test_set.id).order_by(EvalTestCase.created_at)
        )
    )


def _case_count(session: Session, test_set_id: str) -> int:
    return session.exec(select(func.count(EvalTestCase.id)).where(EvalTestCase.test_set_id == test_set_id)).one()


def add_case(
    session: Session, test_set: EvalTestSet, question: str, reference: str | None, kind: str = "topic"
) -> EvalTestCase:
    if _case_count(session, test_set.id) >= MAX_CASES_PER_TEST_SET:
        raise EvaluationError(ErrorCode.EVALUATION_TEST_SET_FULL)
    question_text = _clean_text(question, MAX_QUESTION_CHARS)
    if not question_text:
        raise EvaluationError(ErrorCode.EVALUATION_QUESTION_REQUIRED)
    case = EvalTestCase(
        test_set_id=test_set.id,
        user_id=test_set.user_id,
        question=question_text,
        reference=_clean_text(reference, MAX_REFERENCE_CHARS),
        kind=kind if kind in KINDS else "topic",
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def update_case(session: Session, case: EvalTestCase, changes: dict) -> EvalTestCase:
    if "question" in changes:
        question_text = _clean_text(changes["question"], MAX_QUESTION_CHARS)
        if not question_text:
            raise EvaluationError(ErrorCode.EVALUATION_QUESTION_REQUIRED)
        case.question = question_text
    if "reference" in changes:
        case.reference = _clean_text(changes["reference"], MAX_REFERENCE_CHARS)
    if changes.get("approved") is not None:
        case.approved = bool(changes["approved"])
    if changes.get("kind") in KINDS:
        case.kind = changes["kind"]
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def delete_case(session: Session, case: EvalTestCase) -> None:
    session.delete(case)
    session.commit()


def delete_unapproved(session: Session, test_set: EvalTestSet) -> int:
    """Discard all drafts the teacher didn't approve."""
    result = session.execute(
        delete(EvalTestCase).where(EvalTestCase.test_set_id == test_set.id, EvalTestCase.approved.is_(False))
    )
    session.commit()
    return result.rowcount or 0


# --- CSV import/export -----------------------------------------------------------------------


# What the "kind" column of a CSV may say (the export writes the English names).
_CSV_KINDS = {
    "grounded": "grounded",
    "material": "grounded",
    "topic": "topic",
    "thema": "topic",
    "offtopic": "offtopic",
    "ausserhalb": "offtopic",
    "außerhalb": "offtopic",
}


def _decode_csv(data: bytes) -> str:
    # Excel saves "CSV UTF-8" with a byte-order mark and plain "CSV" in Windows-1252.
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _strip_csv_guard(value: str) -> str:
    """Undo export_csv's formula guard, so an exported file imports unchanged."""
    if len(value) > 1 and value[0] == "'" and value[1] in "=+-@\t\r":
        return value[1:]
    return value


def import_csv(session: Session, test_set: EvalTestSet, data: bytes) -> tuple[int, int]:
    """Add the rows of a `question,reference,kind` CSV (the last two optional); returns
    (imported, skipped)."""
    if len(data) > MAX_CSV_BYTES or b"\x00" in data:
        raise EvaluationError(ErrorCode.EVALUATION_CSV_INVALID)
    text = _decode_csv(data)
    # German Excel writes ";", most other tools ","; the header line tells which. (csv.Sniffer
    # also guesses the quote character, and takes the export's "'" formula guard for one.)
    first_line = text.split("\n", 1)[0]
    delimiter = max(",;\t", key=first_line.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = [cell.strip().lower() for cell in next(reader)]
    except (StopIteration, csv.Error) as exc:
        raise EvaluationError(ErrorCode.EVALUATION_CSV_INVALID) from exc
    question_names = {"question", "frage"}
    reference_names = {"reference", "referenz", "answer", "antwort", "reference_answer", "referenzantwort"}
    kind_names = {"kind", "art", "fragenart"}
    question_col = next((i for i, name in enumerate(header) if name in question_names), None)
    reference_col = next((i for i, name in enumerate(header) if name in reference_names), None)
    kind_col = next((i for i, name in enumerate(header) if name in kind_names), None)
    if question_col is None:
        raise EvaluationError(ErrorCode.EVALUATION_CSV_INVALID)

    room = MAX_CASES_PER_TEST_SET - _case_count(session, test_set.id)
    imported = skipped = 0
    try:
        for row in reader:
            question = _strip_csv_guard(row[question_col]).strip() if question_col < len(row) else ""
            reference = (
                _strip_csv_guard(row[reference_col]).strip() if reference_col is not None and reference_col < len(row) else ""
            )
            kind = _CSV_KINDS.get(row[kind_col].strip().lower(), "topic") if kind_col is not None and kind_col < len(row) else "topic"
            if not question or len(question) > MAX_QUESTION_CHARS or len(reference) > MAX_REFERENCE_CHARS or imported >= room:
                skipped += 1
                continue
            session.add(
                EvalTestCase(
                    test_set_id=test_set.id,
                    user_id=test_set.user_id,
                    question=question,
                    reference=reference or None,
                    kind=kind,
                    origin="csv",
                )
            )
            imported += 1
    except csv.Error as exc:
        session.rollback()
        raise EvaluationError(ErrorCode.EVALUATION_CSV_INVALID) from exc
    session.commit()
    return imported, skipped


def export_csv(session: Session, test_set: EvalTestSet) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["question", "reference", "kind"])
    for case in list_cases(session, test_set):
        if case.approved:
            writer.writerow([_csv_safe(case.question), _csv_safe(case.reference or ""), case.kind])
    return out.getvalue()


# --- Judge -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Judge:
    config: dict  # for the evaluation service, with the decrypted key
    label: str  # for the run's snapshot: provider and model, never the key


def judge_for(session: Session, user_id: str, key_id: str | None) -> Judge:
    key = get_owned_key_of_type(session, user_id, key_id, KEY_TYPE_LLM) if key_id else None
    if key is None or not key.model_id or key.provider == GWDG_ARCANA_PROVIDER:
        raise EvaluationError(ErrorCode.EVALUATION_JUDGE_KEY_INVALID)
    model = build_model_string(key.provider, key.model_id)
    config = {"model": model, "api_key": reveal_api_key(key.encrypted_api_key) or None}
    api_base = effective_api_base(key)
    if api_base:
        config["api_base"] = api_base
    return Judge(config=config, label=f"{provider_label(key.provider)} · {key.model_id}")


# --- Drafting questions ----------------------------------------------------------------------


def generate_cases(
    session: Session,
    test_set: EvalTestSet,
    judge_key_id: str,
    size: int,
    *,
    kind: str = "grounded",
    project: Project | None = None,
    objectives: str | None = None,
) -> list[EvalTestCase]:
    """Draft up to `size` questions. They're saved unapproved; the teacher reads, edits and
    approves them.

    grounded: from a random sample of the knowledge base's passages, with reference answers.
    topic / offtopic: from the project's instructions and the document titles only — the judge
    doesn't see the material, so questions it doesn't cover (topic) or that it can't cover
    (offtopic) come up. They have no reference answer.
    """
    if kind not in KINDS:
        kind = "grounded"
    if kind != "grounded" and project is None:
        raise EvaluationError(ErrorCode.EVALUATION_PROJECT_REQUIRED)
    with _drafting_lock:
        if test_set.user_id in _drafting_users:
            raise EvaluationError(ErrorCode.EVALUATION_GENERATION_ACTIVE, status_code=409)
        _drafting_users.add(test_set.user_id)
    try:
        return _generate_cases(session, test_set, judge_key_id, size, kind, project, objectives)
    finally:
        with _drafting_lock:
            _drafting_users.discard(test_set.user_id)


def _grounded_request(session: Session, test_set: EvalTestSet, size: int) -> dict:
    try:
        chunks = rag_client.sample_chunks(test_set.knowledge_base_id, _CHUNK_SAMPLE)
    except (rag_client.RagUnavailable, rag_client.RagRejected) as exc:
        raise KnowledgeServiceUnavailable() from exc
    used = set(
        session.exec(
            select(EvalTestCase.source_chunk_id).where(
                EvalTestCase.test_set_id == test_set.id, EvalTestCase.source_chunk_id.is_not(None)
            )
        )
    )
    # Passages that already have a question come last, so drafting again mostly asks about others.
    chunks.sort(key=lambda c: c["chunk_id"] in used)
    chunks = [c for c in chunks if c.get("text", "").strip()]
    if not chunks:
        raise EvaluationError(ErrorCode.EVALUATION_NO_PASSAGES)
    return {
        "chunks": [
            {"chunk_id": c["chunk_id"], "text": c["text"][:20000], "heading": c.get("heading"), "page": c.get("page")}
            for c in chunks
        ]
    }


def _topic_request(session: Session, test_set: EvalTestSet, kind: str, project: Project, objectives: str | None) -> dict:
    """What the judge may know about the subject: never the material's content, only how the
    teacher described it and what its documents are called."""
    kb = session.get(KnowledgeBase, test_set.knowledge_base_id)
    existing = session.exec(
        select(EvalTestCase.question)
        .where(EvalTestCase.test_set_id == test_set.id, EvalTestCase.kind == kind)
        .order_by(EvalTestCase.created_at.desc())
        .limit(40)
    ).all()
    return {
        "topic": {
            "preprompt": (project.preprompt or "")[:8000],
            "project_title": project.title[:200],
            "description": ((kb.description or "") if kb else "")[:2000],
            "document_titles": titles_by_document(session, test_set.knowledge_base_id),
            "objectives": (objectives or "").strip()[:2000],
            "existing_questions": list(existing),
        }
    }


def _generate_cases(
    session: Session,
    test_set: EvalTestSet,
    judge_key_id: str,
    size: int,
    kind: str,
    project: Project | None,
    objectives: str | None,
) -> list[EvalTestCase]:
    room = MAX_CASES_PER_TEST_SET - _case_count(session, test_set.id)
    if room <= 0:
        raise EvaluationError(ErrorCode.EVALUATION_TEST_SET_FULL)
    size = max(1, min(size, MAX_DRAFTS_PER_REQUEST, room))
    judge = judge_for(session, test_set.user_id, judge_key_id)
    if kind == "grounded":
        material = _grounded_request(session, test_set, size)
    else:
        material = _topic_request(session, test_set, kind, project, objectives)
    body = {"kind": kind, "size": size, "judge": judge.config, "language": test_set.language, **material}
    try:
        drafts = eval_client.generate_testset(body)
    except eval_client.EvalRejected as exc:
        raise EvaluationError(exc.code, status_code=502) from exc
    except eval_client.EvalUnavailable as exc:
        raise EvaluationServiceUnavailable() from exc
    cases = []
    for draft in drafts[:size]:
        question = _clean_text(draft.get("question"), MAX_QUESTION_CHARS)
        if not question:
            continue
        case = EvalTestCase(
            test_set_id=test_set.id,
            user_id=test_set.user_id,
            question=question,
            # Only grounded drafts have one; the judge's own knowledge is no yardstick for the rest.
            reference=_clean_text(draft.get("reference"), MAX_REFERENCE_CHARS) if kind == "grounded" else None,
            kind=kind,
            origin="generated",
            approved=False,
            source_chunk_id=draft.get("chunk_id") if kind == "grounded" else None,
        )
        session.add(case)
        cases.append(case)
    session.commit()
    for case in cases:
        session.refresh(case)
    return cases


# --- Runs ------------------------------------------------------------------------------------


def max_cases_per_run(session: Session) -> int:
    return get_or_create_site_settings(session).rag_eval_max_cases_per_run


def _snapshot(session: Session, project: Project, test_set: EvalTestSet, judge: Judge, metrics: list[str]) -> dict:
    """What the run measured, readable later even after the project or test set has changed."""
    llm_key = resolve_llm_key(session, project)
    kb_names = []
    for kb_id in project.knowledge_base_ids:
        kb = session.get(KnowledgeBase, kb_id)
        if kb is not None and kb.user_id == project.user_id:
            kb_names.append({"name": kb.name, "embedding_model": kb.embedding_model})
    test_kb = session.get(KnowledgeBase, test_set.knowledge_base_id)
    return {
        "project_title": project.title,
        "llm": f"{provider_label(llm_key.provider)} · {llm_key.model_id}" if llm_key else None,
        "temperature": project.temperature,
        "knowledge_mode": project.knowledge_mode,
        "top_k": project.knowledge_top_k,
        "knowledge_bases": kb_names,
        "test_set_name": test_set.name,
        "test_set_knowledge_base": test_kb.name if test_kb else None,
        "language": test_set.language,
        "judge": judge.label,
        "metrics": metrics,
    }


def start_run(
    session: Session, user_id: str, project: Project, test_set: EvalTestSet, judge_key_id: str, metrics: list[str]
) -> EvalRun:
    # Canonical order; the request schema already guarantees at least one known metric.
    metrics = [m for m in METRICS if m in set(metrics)]
    judge = judge_for(session, user_id, judge_key_id)
    if resolve_llm_key(session, project) is None:
        raise EvaluationError(ErrorCode.NO_LLM_MODEL_SELECTED)
    cases = [c for c in list_cases(session, test_set) if c.approved]
    if not cases:
        raise EvaluationError(ErrorCode.EVALUATION_NO_CASES)
    if len(cases) > max_cases_per_run(session):
        raise EvaluationError(ErrorCode.EVALUATION_TOO_MANY_CASES)
    knowledge_base_ids = sorted({*project.knowledge_base_ids, test_set.knowledge_base_id})
    with _run_start_lock:
        active = session.exec(
            select(EvalRun.id).where(EvalRun.user_id == user_id, EvalRun.status.in_(ACTIVE_STATUSES))
        ).first()
        if active:
            # One at a time per teacher: runs share a single worker (see runner.py), and a second
            # one would only wait in line while already counting against the teacher's key budget.
            raise EvaluationError(ErrorCode.EVALUATION_RUN_ACTIVE, status_code=409)

        run = EvalRun(
            user_id=user_id,
            project_id=project.id,
            test_set_id=test_set.id,
            judge_api_key_id=judge_key_id,
            config_json=json.dumps(_snapshot(session, project, test_set, judge, metrics), ensure_ascii=False),
            metrics_json=json.dumps(metrics),
            knowledge_base_ids_json=json.dumps(knowledge_base_ids),
            case_count=len(cases),
        )
        session.add(run)
        for position, case in enumerate(cases):
            session.add(
                EvalRunItem(
                    run_id=run.id,
                    test_case_id=case.id,
                    position=position,
                    question=case.question,
                    reference=case.reference,
                    kind=case.kind,
                )
            )
        session.commit()
    session.refresh(run)
    return run


def list_runs(session: Session, user_id: str, project_id: str | None = None) -> list[EvalRun]:
    query = select(EvalRun).where(EvalRun.user_id == user_id)
    if project_id:
        query = query.where(EvalRun.project_id == project_id)
    return list(session.exec(query.order_by(EvalRun.created_at.desc())))


def run_items(session: Session, run: EvalRun) -> list[EvalRunItem]:
    return list(session.exec(select(EvalRunItem).where(EvalRunItem.run_id == run.id).order_by(EvalRunItem.position)))


def cancel_run(session: Session, run: EvalRun) -> EvalRun:
    """Stops the run after the question or batch in progress; what's scored so far is kept."""
    if run.status not in ACTIVE_STATUSES:
        raise EvaluationError(ErrorCode.EVALUATION_RUN_NOT_ACTIVE, status_code=409)
    run.status = "cancelled"
    run.finished_at = _now()
    run.summary_json = json.dumps(summarize(run.metrics, run_items(session, run)))
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def delete_run(session: Session, run: EvalRun) -> None:
    # A running run notices on its next step that its row is gone and stops (see runner.py).
    session.execute(delete(EvalRunItem).where(EvalRunItem.run_id == run.id))
    session.delete(run)
    session.commit()


def mark_interrupted(session: Session) -> int:
    """At startup: runs that were in progress when the backend stopped can't continue."""
    runs = list(session.exec(select(EvalRun).where(EvalRun.status.in_(ACTIVE_STATUSES))))
    for run in runs:
        run.status, run.error_code, run.finished_at = "interrupted", ErrorCode.EVALUATION_INTERRUPTED, _now()
        run.summary_json = json.dumps(summarize(run.metrics, run_items(session, run)))
        session.add(run)
    session.commit()
    return len(runs)


# --- Results ---------------------------------------------------------------------------------


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 1)


def _metric_summary(metrics: list[str], items: list[EvalRunItem]) -> dict:
    per_metric = {}
    for metric in metrics:
        values = []
        for item in items:
            score = (json.loads(item.scores_json) if item.scores_json else {}).get(metric) or {}
            if score.get("value") is not None:
                values.append(score["value"])
        per_metric[metric] = {
            "mean": round(statistics.fmean(values), 4) if values else None,
            "median": round(statistics.median(values), 4) if values else None,
            "count": len(values),
        }
    return per_metric


def summarize(metrics: list[str], items: list[EvalRunItem]) -> dict:
    """Mean and median per metric over the questions it applied to — overall and per question
    kind — plus latency percentiles."""
    retrieval = [i.retrieval_ms for i in items if i.retrieval_ms is not None]
    llm_times = [i.llm_ms for i in items if i.llm_ms is not None]
    by_kind = {}
    for kind in KINDS:
        of_kind = [i for i in items if i.kind == kind]
        if of_kind:
            by_kind[kind] = {"count": len(of_kind), "metrics": _metric_summary(metrics, of_kind)}
    return {
        "metrics": _metric_summary(metrics, items),
        "by_kind": by_kind,
        "retrieval_ms": {"p50": _percentile(retrieval, 0.5), "p90": _percentile(retrieval, 0.9)},
        "llm_ms": {"p50": _percentile(llm_times, 0.5), "p90": _percentile(llm_times, 0.9)},
    }


def item_out(item: EvalRunItem) -> dict:
    return {
        "id": item.id,
        "position": item.position,
        "question": item.question,
        "reference": item.reference,
        "kind": item.kind,
        "answer": item.answer,
        "contexts": json.loads(item.contexts_json or "[]"),
        "scores": json.loads(item.scores_json) if item.scores_json else {},
        "retrieval_ms": item.retrieval_ms,
        "llm_ms": item.llm_ms,
        "error_code": item.error_code,
    }


def export_run_csv(run: EvalRun, items: list[EvalRunItem]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    metrics = run.metrics
    writer.writerow(["question", "kind", "reference", "answer", *metrics, "retrieval_ms", "llm_ms", "sources", "error"])
    for item in items:
        data = item_out(item)
        sources = "; ".join(
            f"{c.get('filename') or '?'} S. {c['page']}" if c.get("page") else (c.get("filename") or "?")
            for c in data["contexts"]
        )
        scores = [
            "" if (data["scores"].get(m) or {}).get("value") is None else data["scores"][m]["value"] for m in metrics
        ]
        writer.writerow(
            [
                _csv_safe(item.question),
                item.kind,
                _csv_safe(item.reference or ""),
                _csv_safe(item.answer or ""),
                *scores,
                "" if item.retrieval_ms is None else round(item.retrieval_ms),
                "" if item.llm_ms is None else round(item.llm_ms),
                _csv_safe(sources),
                item.error_code or "",
            ]
        )
    return out.getvalue()
