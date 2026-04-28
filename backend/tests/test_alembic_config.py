from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_has_single_knowledge_tables_head_revision() -> None:
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["202604280003"]
    assert Path("alembic/env.py").exists()
