import inspect

from app.core.queue import IncidentQueue
from app.infra import make_redis


def test_redis_socket_timeout_outlasts_queue_blocking_read():
    block_ms = inspect.signature(IncidentQueue.consume).parameters["block_ms"].default
    client = make_redis("redis://localhost:6379/0")
    assert client.connection_pool.connection_kwargs["socket_timeout"] > block_ms / 1000


def test_offline_settings_ignore_developer_env_file(tmp_path, monkeypatch):
    from scripts.demo_offline import offline_settings

    (tmp_path / ".env").write_text("SLACK_BOT_TOKEN=xoxb-real\nOPT_LLM_CACHE=false\n")
    monkeypatch.chdir(tmp_path)
    s = offline_settings()
    assert s.slack_bot_token == "" and s.opt_llm_cache is True
