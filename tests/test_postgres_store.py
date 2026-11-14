from fastapi.testclient import TestClient

from app.main import create_app
from app.rag.loader import load_markdown_dir
from tests.test_pipeline import HEADERS, INCIDENT, _drain


def _rows(c, sql, *params):
    with c.db.connection() as conn:
        return conn.execute(sql, params).fetchall()


def test_migration_creates_tables_and_extensions(container):
    tables = {r["tablename"] for r in _rows(container, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert {"incidents", "documents", "fixes", "approvals", "actions", "llm_calls"} <= tables
    exts = {r["extname"] for r in _rows(container, "SELECT extname FROM pg_extension")}
    assert {"vector", "pg_trgm"} <= exts


def test_one_incident_keeps_its_calls_and_fix_attempts(container, llm_calls):
    container.ingestor.ingest(load_markdown_dir("knowledge"))
    client = TestClient(create_app(container))
    inc_id = client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS).json()["incident_id"]
    final = _drain(container)[0]

    [inc] = _rows(container, "SELECT status, service, category, attempts, finished_at FROM incidents WHERE id = %s", inc_id)
    assert (inc["status"], inc["service"], inc["category"], inc["attempts"]) == ("resolved", "checkout", "code_bug", 2)
    assert inc["finished_at"] is not None

    calls = _rows(container, "SELECT model FROM llm_calls WHERE incident_id = %s AND NOT cache_hit", inc_id)
    assert sorted(r["model"] for r in calls) == sorted(m for m, _ in llm_calls)

    fixes = _rows(container, "SELECT attempt, passed FROM fixes WHERE incident_id = %s ORDER BY id", inc_id)
    assert [f["passed"] for f in fixes] == [False, True], "실패한 첫 시도와 통과한 두 번째 시도"
    assert final["cost"]["usd"] > 0


def test_service_breakdown_is_one_query(container):
    client = TestClient(create_app(container))
    client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS)
    client.post("/api/v1/incidents", json={"channel": "C1", "service": "payment", "message": "PG timeout"}, headers=HEADERS)
    _drain(container)

    rows = _rows(container, """
        SELECT i.service, count(DISTINCT i.id) AS incidents, coalesce(sum(c.usd), 0) AS usd
        FROM incidents i LEFT JOIN llm_calls c ON c.incident_id = i.id
        GROUP BY i.service ORDER BY i.service""")
    assert [(r["service"], r["incidents"]) for r in rows] == [("checkout", 1), ("payment", 1)]
    assert all(r["usd"] > 0 for r in rows)


def test_reused_fix_keeps_its_risk(container):
    client = TestClient(create_app(container))
    first = client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS).json()
    _drain(container)
    container.redis.delete(f"dedupe:{first['fingerprint']}")

    client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS)
    final = _drain(container)[0]
    assert final["reused_fix"] and final["fix"]["risk"]


def test_retired_fix_is_kept_but_not_reused(container):
    client = TestClient(create_app(container))
    first = client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS).json()
    _drain(container)
    container.fixes.retire_all()
    container.redis.delete(f"dedupe:{first['fingerprint']}")

    client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS)
    final = _drain(container)[0]
    assert not final["reused_fix"]
    assert len(_rows(container, "SELECT 1 FROM fixes WHERE incident_id = %s", first["incident_id"])) == 2, "이력은 남는다"
