"""Tests for the project export/import round-trip (app/features/projects/export.py) —
in particular that secrets (API keys, chat password hash) never leave in an export, that a
freshly imported project is always an unpublished draft, and that avatar/background library
references are dropped rather than carried over when they don't belong to the importing user."""

from sqlmodel import Session, SQLModel, create_engine

import app.db.base  # noqa: F401  (registers every model's table on SQLModel.metadata)
from app.features.media.models import AvatarModel
from app.features.projects.export import (
    ProjectImportError,
    export_project_yaml,
    import_project,
    parse_project_yaml,
)
from app.features.projects.models import Project


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_project(**overrides) -> Project:
    fields = dict(
        user_id="owner",
        title="My Project",
        preprompt="be nice",
        temperature=0.7,
        top_p=0.9,
        llm_api_key_id="secret-key-id",
        tts_api_key_id="secret-tts-key-id",
        chat_password_hash="bcrypt-hash",
        published=True,
        share_slug="abc123",
    )
    fields.update(overrides)
    return Project(**fields)


def test_export_omits_secrets_and_publishing_state() -> None:
    with _make_session() as session:
        project = _make_project()
        session.add(project)
        session.commit()
        session.refresh(project)

        yaml_text = export_project_yaml(project)

    assert "secret-key-id" not in yaml_text
    assert "secret-tts-key-id" not in yaml_text
    assert "bcrypt-hash" not in yaml_text
    assert "abc123" not in yaml_text
    assert "published" not in yaml_text
    assert "temperature: 0.7" in yaml_text


def test_import_creates_an_unpublished_draft_without_the_original_keys() -> None:
    with _make_session() as session:
        project = _make_project()
        session.add(project)
        session.commit()
        session.refresh(project)

        data = parse_project_yaml(export_project_yaml(project))
        imported = import_project(session, "other-user", data)

        assert imported.id != project.id
        assert imported.user_id == "other-user"
        assert imported.title == "My Project"
        assert imported.temperature == 0.7
        assert imported.published is False
        assert imported.share_slug is None
        assert imported.llm_api_key_id is None
        assert imported.chat_password_hash is None


def test_import_drops_avatar_reference_not_owned_by_the_importing_user() -> None:
    with _make_session() as session:
        avatar = AvatarModel(id="av1", user_id="owner", name="A", file_path="/tmp/a.glb")
        session.add(avatar)
        project = _make_project(avatar_model_id="av1")
        session.add(project)
        session.commit()
        session.refresh(project)

        data = parse_project_yaml(export_project_yaml(project))

        imported_for_stranger = import_project(session, "stranger", data)
        assert imported_for_stranger.avatar_model_id is None

        imported_for_owner = import_project(session, "owner", data)
        assert imported_for_owner.avatar_model_id == "av1"
        assert imported_for_owner.avatar_model_url == "/api/v1/avatars/av1/file"


def test_export_is_format_version_2_and_keeps_a_builtin_avatar() -> None:
    with _make_session() as session:
        project = _make_project(builtin_avatar="david")
        session.add(project)
        session.commit()
        session.refresh(project)

        yaml_text = export_project_yaml(project)
        imported = import_project(session, "stranger", parse_project_yaml(yaml_text))

    assert "lola_export: 2" in yaml_text
    assert imported.builtin_avatar == "david"
    assert imported.avatar_model_url == "/avatars/david.glb"


def test_import_reads_version_1_files_with_urls() -> None:
    v1_file = """
lola_export: 1
project:
  title: Old Export
  avatar_model_url: /avatar-models/av1/file
  avatar_background_url: /backgrounds/bg1/file
"""
    data = parse_project_yaml(v1_file)
    assert (data.avatar_model_id, data.builtin_avatar, data.avatar_background_id) == ("av1", None, "bg1")

    builtin = parse_project_yaml("lola_export: 1\nproject:\n  title: x\n  avatar_model_url: /avatars/julia.glb\n")
    assert (builtin.avatar_model_id, builtin.builtin_avatar) == (None, "julia")

    unknown = parse_project_yaml(
        "lola_export: 1\nproject:\n  title: x\n  avatar_model_url: https://evil.example/x.glb\n"
    )
    assert (unknown.avatar_model_id, unknown.builtin_avatar) == (None, None)


def test_chat_layout_round_trips_and_older_exports_map_chat_default_open() -> None:
    with _make_session() as session:
        project = _make_project(chat_layout="chat_only")
        session.add(project)
        session.commit()
        session.refresh(project)
        imported = import_project(session, "owner", parse_project_yaml(export_project_yaml(project)))
    assert imported.chat_layout == "chat_only"

    collapsed = parse_project_yaml("lola_export: 2\nproject:\n  title: x\n  chat_default_open: false\n")
    assert collapsed.chat_layout == "avatar_chat_collapsed"
    opened = parse_project_yaml("lola_export: 2\nproject:\n  title: x\n  chat_default_open: true\n")
    assert opened.chat_layout == "avatar_chat"
    with_both = parse_project_yaml(
        "lola_export: 2\nproject:\n  title: x\n  chat_default_open: false\n  chat_layout: avatar_only\n"
    )
    assert with_both.chat_layout == "avatar_only"


def test_parse_rejects_malformed_or_incomplete_input() -> None:
    for bad_input in ("not: [valid", "no_project_key: true", "project: {temperature: 99}", "project: {}",
                      "project: {title: x, builtin_avatar: ../../etc}"):
        try:
            parse_project_yaml(bad_input)
            raise AssertionError(f"expected ProjectImportError for input: {bad_input!r}")
        except ProjectImportError:
            pass
