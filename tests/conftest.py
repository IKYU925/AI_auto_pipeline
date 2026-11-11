import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.container import Container  # noqa: E402
from app.llm.fake import fake_model_factory  # noqa: E402
from scripts.demo_offline import offline_settings  # noqa: E402
from scripts.demo_responder import demo_responder  # noqa: E402


@pytest.fixture
def llm_calls() -> list:
    return []


@pytest.fixture
def container(tmp_path, llm_calls) -> Container:
    return Container(
        offline_settings(artifacts_dir=str(tmp_path / "artifacts")),
        model_factory=fake_model_factory(demo_responder, llm_calls),
    )
