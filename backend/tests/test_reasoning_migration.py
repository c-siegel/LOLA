"""b2c3d4e5f6a7 turns Project.thinking_mode into reasoning_mode without losing anyone's choice."""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.core.config import settings


def _insert_project(connection, project_id: str, **values) -> None:
    """A project row with placeholders for every NOT NULL column that has no default."""
    row = dict(values, id=project_id)
    for _, name, kind, notnull, default, _pk in connection.exec_driver_sql("PRAGMA table_info(project)").fetchall():
        if name not in row and notnull and default is None:
            row[name] = 0 if "INT" in kind.upper() or "BOOL" in kind.upper() or "FLOAT" in kind.upper() else "x"
    columns = ", ".join(row)
    placeholders = ", ".join(f":{name}" for name in row)
    connection.execute(sa.text(f"INSERT INTO project ({columns}) VALUES ({placeholders})"), row)


def test_thinking_mode_values_carry_over_and_back(tmp_path, monkeypatch) -> None:
    db = tmp_path / "migrated.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db}")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    engine = sa.create_engine(f"sqlite:///{db}")

    command.upgrade(config, "a1b2c3d4e5f6")
    with engine.begin() as connection:
        for project_id, mode in (("p-on", "on"), ("p-off", "off"), ("p-default", "default")):
            _insert_project(connection, project_id, thinking_mode=mode)

    command.upgrade(config, "b2c3d4e5f6a7")
    with engine.connect() as connection:
        modes = dict(connection.execute(sa.text("SELECT id, reasoning_mode FROM project")).fetchall())
        efforts = connection.execute(sa.text("SELECT DISTINCT reasoning_effort FROM project")).fetchall()
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(project)")}
    assert modes == {"p-on": "always", "p-off": "off", "p-default": "default"}
    assert efforts == [(None,)]
    assert "thinking_mode" not in columns

    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE project SET reasoning_mode = 'adaptive' WHERE id = 'p-default'"))
    command.downgrade(config, "a1b2c3d4e5f6")
    with engine.connect() as connection:
        modes = dict(connection.execute(sa.text("SELECT id, thinking_mode FROM project")).fetchall())
    assert modes == {"p-on": "on", "p-off": "off", "p-default": "default"}


def test_second_key_columns_come_and_go(tmp_path, monkeypatch) -> None:
    db = tmp_path / "second_key.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db}")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    engine = sa.create_engine(f"sqlite:///{db}")

    command.upgrade(config, "b2c3d4e5f6a7")
    with engine.begin() as connection:
        _insert_project(connection, "existing")

    command.upgrade(config, "c3d4e5f6a7b8")
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT deep_llm_api_key_id, deep_llm_model, bridge_enabled FROM project WHERE id = 'existing'")
        ).one()
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_key_list(project)").fetchall()
    assert tuple(row) == (None, None, 1)  # existing projects get the bridge phrase
    assert any(fk[3] == "deep_llm_api_key_id" and fk[2] == "userapikey" for fk in foreign_keys)

    command.downgrade(config, "b2c3d4e5f6a7")
    with engine.connect() as connection:
        columns = {r[1] for r in connection.exec_driver_sql("PRAGMA table_info(project)")}
    assert not columns & {"deep_llm_api_key_id", "deep_llm_model", "bridge_enabled"}
