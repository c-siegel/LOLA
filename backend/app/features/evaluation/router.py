"""
Evaluation Routes

Measuring answer quality with Ragas (see docs/rag-plan.md §7): test sets per knowledge base, and
runs that answer a test set through one of the teacher's projects and have the answers scored.
Every route answers 404 EVALUATION_DISABLED unless the deployment enables both the knowledge
base and its evaluation (Settings.evaluation_enabled) — except /providers/evaluation-status,
which tells the dashboard whether to show any of this. Everything is scoped to the teacher's
own knowledge bases, projects and keys.
"""

import json

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlmodel import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_session
from app.core.error_codes import ErrorCode
from app.core.ownership import get_owned
from app.core.rate_limit import enforce_evaluation_rate_limit
from app.features.evaluation import eval_client, runner, service
from app.features.evaluation.models import EvalRun, EvalTestCase, EvalTestSet
from app.features.evaluation.schemas import (
    CsvImportOut,
    EvaluationStatusOut,
    GenerateIn,
    RunCreate,
    RunDetailOut,
    RunOut,
    TestCaseCreate,
    TestCaseOut,
    TestCaseUpdate,
    TestSetCreate,
    TestSetOut,
    TestSetUpdate,
)
from app.features.knowledge.models import KnowledgeBase
from app.features.projects.models import Project
from app.features.users.models import User

router = APIRouter(tags=["evaluation"])


def _enabled() -> None:
    service.require_enabled()


def _test_set_out(test_set: EvalTestSet, counts: dict[str, tuple[int, int]]) -> TestSetOut:
    total, approved = counts.get(test_set.id, (0, 0))
    return TestSetOut(
        id=test_set.id,
        knowledge_base_id=test_set.knowledge_base_id,
        name=test_set.name,
        language=test_set.language,
        case_count=total,
        approved_count=approved,
        created_at=test_set.created_at,
    )


def _case_out(case: EvalTestCase) -> TestCaseOut:
    return TestCaseOut.model_validate(case, from_attributes=True)


def _run_out(run: EvalRun) -> RunOut:
    return RunOut(
        id=run.id,
        project_id=run.project_id,
        test_set_id=run.test_set_id,
        status=run.status,
        error_code=run.error_code,
        metrics=run.metrics,
        config=run.config,
        case_count=run.case_count,
        answered_count=run.answered_count,
        scored_count=run.scored_count,
        summary=json.loads(run.summary_json) if run.summary_json else None,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


@router.get("/providers/evaluation-status", response_model=EvaluationStatusOut)
def evaluation_status(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """Whether this deployment offers evaluation, and what the run form needs for its estimate."""
    if not settings.evaluation_enabled:
        return EvaluationStatusOut(available=False)
    out = EvaluationStatusOut(
        available=True,
        max_cases_per_run=service.max_cases_per_run(session),
        max_drafts_per_request=service.MAX_DRAFTS_PER_REQUEST,
        metrics=list(service.METRICS),
        judge_calls_per_metric=service.JUDGE_CALLS_PER_METRIC,
    )
    try:
        out.ragas_version = eval_client.health().get("ragas_version")
        out.reachable = True
    except (eval_client.EvalUnavailable, eval_client.EvalRejected):
        pass
    return out


# --- Test sets -------------------------------------------------------------------------------


@router.get("/knowledge-bases/{kb_id}/test-sets", response_model=list[TestSetOut], dependencies=[Depends(_enabled)])
def list_test_sets(kb_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    kb = get_owned(session, KnowledgeBase, kb_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    test_sets = service.list_test_sets(session, kb)
    counts = service.case_counts(session, [t.id for t in test_sets])
    return [_test_set_out(t, counts) for t in test_sets]


@router.get("/test-sets", response_model=list[TestSetOut], dependencies=[Depends(_enabled)])
def list_all_test_sets(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """All the teacher's test sets, for the run form."""
    test_sets = service.list_user_test_sets(session, current_user.id)
    counts = service.case_counts(session, [t.id for t in test_sets])
    return [_test_set_out(t, counts) for t in test_sets]


@router.post(
    "/knowledge-bases/{kb_id}/test-sets", response_model=TestSetOut, status_code=201, dependencies=[Depends(_enabled)]
)
def create_test_set(
    kb_id: str, data: TestSetCreate, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    kb = get_owned(session, KnowledgeBase, kb_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    return _test_set_out(service.create_test_set(session, current_user.id, kb, data.name, data.language), {})


@router.patch("/test-sets/{test_set_id}", response_model=TestSetOut, dependencies=[Depends(_enabled)])
def update_test_set(
    test_set_id: str,
    data: TestSetUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    test_set = service.update_test_set(
        session,
        get_owned(session, EvalTestSet, test_set_id, current_user.id, ErrorCode.EVALUATION_TEST_SET_NOT_FOUND),
        data.name,
        data.language,
    )
    return _test_set_out(test_set, service.case_counts(session, [test_set.id]))


@router.delete("/test-sets/{test_set_id}", status_code=204, dependencies=[Depends(_enabled)])
def delete_test_set(
    test_set_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    """Delete a test set with its questions and every run that used it."""
    test_set = get_owned(session, EvalTestSet, test_set_id, current_user.id, ErrorCode.EVALUATION_TEST_SET_NOT_FOUND)
    service.delete_test_set(session, test_set)


# --- Test cases ------------------------------------------------------------------------------


@router.get("/test-sets/{test_set_id}/cases", response_model=list[TestCaseOut], dependencies=[Depends(_enabled)])
def list_cases(test_set_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    test_set = get_owned(session, EvalTestSet, test_set_id, current_user.id, ErrorCode.EVALUATION_TEST_SET_NOT_FOUND)
    return [_case_out(c) for c in service.list_cases(session, test_set)]


@router.post(
    "/test-sets/{test_set_id}/cases", response_model=TestCaseOut, status_code=201, dependencies=[Depends(_enabled)]
)
def add_case(
    test_set_id: str,
    data: TestCaseCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    test_set = get_owned(session, EvalTestSet, test_set_id, current_user.id, ErrorCode.EVALUATION_TEST_SET_NOT_FOUND)
    return _case_out(service.add_case(session, test_set, data.question, data.reference, data.kind))


@router.post("/test-sets/{test_set_id}/cases/import", response_model=CsvImportOut, dependencies=[Depends(_enabled)])
def import_cases(
    test_set_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Add questions from a CSV with the columns `question` and (optional) `reference`."""
    test_set = get_owned(session, EvalTestSet, test_set_id, current_user.id, ErrorCode.EVALUATION_TEST_SET_NOT_FOUND)
    data = file.file.read(service.MAX_CSV_BYTES + 1)
    imported, skipped = service.import_csv(session, test_set, data)
    return CsvImportOut(imported=imported, skipped=skipped)


@router.get("/test-sets/{test_set_id}/cases/export", dependencies=[Depends(_enabled)])
def export_cases(test_set_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """The approved questions as CSV, in the format the import reads."""
    test_set = get_owned(session, EvalTestSet, test_set_id, current_user.id, ErrorCode.EVALUATION_TEST_SET_NOT_FOUND)
    return Response(
        content=service.export_csv(session, test_set).encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="test-set.csv"'},
    )


@router.post(
    "/test-sets/{test_set_id}/generate",
    response_model=list[TestCaseOut],
    status_code=201,
    dependencies=[Depends(_enabled)],
)
def generate_cases(
    test_set_id: str,
    data: GenerateIn,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Draft questions with reference answers from the material, with the teacher's judge key.
    Drafts are unapproved until the teacher approves them."""
    test_set = get_owned(session, EvalTestSet, test_set_id, current_user.id, ErrorCode.EVALUATION_TEST_SET_NOT_FOUND)
    get_owned(session, KnowledgeBase, test_set.knowledge_base_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    project = None
    if data.project_id:
        project = get_owned(session, Project, data.project_id, current_user.id, ErrorCode.PROJECT_NOT_FOUND)
    enforce_evaluation_rate_limit(current_user.id)
    cases = service.generate_cases(
        session,
        test_set,
        data.judge_api_key_id,
        data.size,
        kind=data.kind,
        project=project,
        objectives=data.objectives,
    )
    return [_case_out(c) for c in cases]


@router.delete("/test-sets/{test_set_id}/drafts", status_code=204, dependencies=[Depends(_enabled)])
def discard_drafts(
    test_set_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    test_set = get_owned(session, EvalTestSet, test_set_id, current_user.id, ErrorCode.EVALUATION_TEST_SET_NOT_FOUND)
    service.delete_unapproved(session, test_set)


@router.patch("/test-cases/{case_id}", response_model=TestCaseOut, dependencies=[Depends(_enabled)])
def update_case(
    case_id: str,
    data: TestCaseUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    case = get_owned(session, EvalTestCase, case_id, current_user.id, ErrorCode.EVALUATION_TEST_CASE_NOT_FOUND)
    return _case_out(service.update_case(session, case, data.model_dump(exclude_unset=True)))


@router.delete("/test-cases/{case_id}", status_code=204, dependencies=[Depends(_enabled)])
def delete_case(case_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    case = get_owned(session, EvalTestCase, case_id, current_user.id, ErrorCode.EVALUATION_TEST_CASE_NOT_FOUND)
    service.delete_case(session, case)


# --- Runs ------------------------------------------------------------------------------------


@router.get("/evaluation/runs", response_model=list[RunOut], dependencies=[Depends(_enabled)])
def list_runs(
    project_id: str | None = Query(default=None, alias="projectId"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return [_run_out(r) for r in service.list_runs(session, current_user.id, project_id)]


@router.post("/evaluation/runs", response_model=RunOut, status_code=202, dependencies=[Depends(_enabled)])
def start_run(data: RunCreate, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """Start a run in the background; poll GET /evaluation/runs/{id} for progress."""
    project = get_owned(session, Project, data.project_id, current_user.id, ErrorCode.PROJECT_NOT_FOUND)
    test_set = get_owned(
        session, EvalTestSet, data.test_set_id, current_user.id, ErrorCode.EVALUATION_TEST_SET_NOT_FOUND
    )
    enforce_evaluation_rate_limit(current_user.id)
    run = service.start_run(session, current_user.id, project, test_set, data.judge_api_key_id, list(data.metrics))
    runner.submit(run.id)
    return _run_out(run)


@router.get("/evaluation/runs/{run_id}", response_model=RunDetailOut, dependencies=[Depends(_enabled)])
def get_run(run_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    run = get_owned(session, EvalRun, run_id, current_user.id, ErrorCode.EVALUATION_RUN_NOT_FOUND)
    items = [service.item_out(i) for i in service.run_items(session, run)]
    return RunDetailOut(**_run_out(run).model_dump(), items=items)


@router.post("/evaluation/runs/{run_id}/cancel", response_model=RunOut, dependencies=[Depends(_enabled)])
def cancel_run(run_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    run = get_owned(session, EvalRun, run_id, current_user.id, ErrorCode.EVALUATION_RUN_NOT_FOUND)
    return _run_out(service.cancel_run(session, run))


@router.delete("/evaluation/runs/{run_id}", status_code=204, dependencies=[Depends(_enabled)])
def delete_run(run_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    run = get_owned(session, EvalRun, run_id, current_user.id, ErrorCode.EVALUATION_RUN_NOT_FOUND)
    service.delete_run(session, run)


@router.get("/evaluation/runs/{run_id}/export", dependencies=[Depends(_enabled)])
def export_run(
    run_id: str,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    run = get_owned(session, EvalRun, run_id, current_user.id, ErrorCode.EVALUATION_RUN_NOT_FOUND)
    items = service.run_items(session, run)
    if format == "json":
        body = RunDetailOut(**_run_out(run).model_dump(), items=[service.item_out(i) for i in items])
        return Response(
            content=body.model_dump_json(by_alias=True, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="evaluation-{run.id[:8]}.json"'},
        )
    return Response(
        content=service.export_run_csv(run, items).encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="evaluation-{run.id[:8]}.csv"'},
    )
