import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.container import Container  # noqa: E402
from app.db import ensure_database, migrate, truncate_all  # noqa: E402
from app.llm.fake import fake_model_factory  # noqa: E402
from scripts.demo_offline import TEST_DATABASE_URL, offline_settings  # noqa: E402
from scripts.demo_responder import demo_responder  # noqa: E402


@pytest.fixture
def llm_calls() -> list:
    return []


@pytest.fixture(scope="session")
def database() -> str:
    ensure_database(TEST_DATABASE_URL)
    migrate(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
def container(tmp_path, llm_calls, database) -> Container:
    c = Container(
        offline_settings(artifacts_dir=str(tmp_path / "artifacts")),
        model_factory=fake_model_factory(demo_responder, llm_calls),
    )
    truncate_all(c.db)
    yield c
    c.db.close()
