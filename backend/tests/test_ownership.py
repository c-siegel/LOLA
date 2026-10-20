"""Tests for the shared ownership check (app/core/ownership.py) that every route loading a
teacher's own resource goes through. Someone else's row must look exactly like a missing one."""

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.db.base  # noqa: F401  (registers every model's table on SQLModel.metadata)
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.core.ownership import find_owned, get_owned
from app.features.projects.models import Project
from conftest import create_key, login_as, make_user


@pytest.fixture
def session_with_project():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        project = Project(user_id="owner", title="Mine")
        session.add(project)
        session.commit()
        session.refresh(project)
        yield session, project


def test_find_owned_returns_the_owners_row(session_with_project) -> None:
    session, project = session_with_project
    assert find_owned(session, Project, project.id, "owner") is project


@pytest.mark.parametrize("item_id, user_id", [("missing-id", "owner"), (None, "someone-else")])
def test_find_owned_returns_none_for_missing_or_foreign_rows(session_with_project, item_id, user_id) -> None:
    session, project = session_with_project
    assert find_owned(session, Project, item_id or project.id, user_id) is None


@pytest.mark.parametrize("item_id, user_id", [("missing-id", "owner"), (None, "someone-else")])
def test_get_owned_raises_the_same_404_for_missing_and_foreign_rows(session_with_project, item_id, user_id) -> None:
    session, project = session_with_project
    with pytest.raises(DomainError) as exc_info:
        get_owned(session, Project, item_id or project.id, user_id, ErrorCode.PROJECT_NOT_FOUND)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == ErrorCode.PROJECT_NOT_FOUND


def test_foreign_api_key_route_answers_like_a_missing_one(client, engine, teacher) -> None:
    key = create_key(client)
    other = login_as(client, make_user(engine, email="other@example.com"))

    foreign = other.post(f"/api-keys/{key['id']}/test")
    missing = other.post("/api-keys/does-not-exist/test")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": ErrorCode.API_KEY_NOT_FOUND}
