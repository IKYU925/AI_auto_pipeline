"""plan.md 8장 스키마. incidents의 result, report, updated_at은 결정 0012, fixes.risk는 재사용 수정안의 위험도 표시용, fixes.retired_at은 배포 등으로 재사용 대상에서 빠진 시각.

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE incidents (
  id                text PRIMARY KEY,
  fingerprint       text NOT NULL,
  source            text NOT NULL,
  alertname         text,
  alert_fingerprint text,
  tenant            text NOT NULL DEFAULT 'common',
  service           text NOT NULL,
  severity          text,
  message           text NOT NULL,
  stacktrace        text,
  category          text,
  needs_code_fix    boolean,
  status            text NOT NULL,
  attempts          int DEFAULT 0,
  report            text,
  report_ts         text,
  result            jsonb NOT NULL DEFAULT '{}',
  created_at        timestamptz DEFAULT now(),
  updated_at        timestamptz DEFAULT now(),
  finished_at       timestamptz,
  alert_resolved_at timestamptz
);
CREATE INDEX ON incidents (fingerprint, created_at DESC);
CREATE INDEX ON incidents (alert_fingerprint) WHERE alert_fingerprint IS NOT NULL;

CREATE TABLE documents (
  id           bigserial PRIMARY KEY,
  doc_id       text NOT NULL,
  chunk_hash   text NOT NULL,
  content      text NOT NULL,
  service      text NOT NULL,
  category     text NOT NULL,
  doc_type     text NOT NULL,
  updated_at   timestamptz NOT NULL,
  embed_model  text NOT NULL,
  embedding    vector(384) NOT NULL,
  UNIQUE (doc_id, chunk_hash)
);
CREATE INDEX ON documents USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON documents USING gin (content gin_trgm_ops);

CREATE TABLE fixes (
  id           bigserial PRIMARY KEY,
  incident_id  text REFERENCES incidents(id),
  fingerprint  text NOT NULL,
  attempt      int NOT NULL,
  patch        text NOT NULL,
  explanation  text,
  risk         text,
  test_summary text,
  passed       boolean NOT NULL,
  created_at   timestamptz DEFAULT now(),
  retired_at   timestamptz
);
CREATE INDEX ON fixes (fingerprint) WHERE passed AND retired_at IS NULL;

CREATE TABLE approvals (
  id          bigserial PRIMARY KEY,
  fix_id      bigint REFERENCES fixes(id),
  approver    text NOT NULL,
  decision    text NOT NULL,
  decided_at  timestamptz DEFAULT now()
);

CREATE TABLE actions (
  id          bigserial PRIMARY KEY,
  incident_id text REFERENCES incidents(id),
  playbook    text NOT NULL,
  target      text NOT NULL,
  approver    text NOT NULL,
  approved_at timestamptz NOT NULL,
  status      text NOT NULL,
  log         text,
  finished_at timestamptz,
  UNIQUE (incident_id, playbook)
);

CREATE TABLE llm_calls (
  id          bigserial PRIMARY KEY,
  incident_id text REFERENCES incidents(id),
  task        text NOT NULL,
  model       text NOT NULL,
  tokens_in   int,
  tokens_out  int,
  usd         numeric(10, 6),
  cache_hit   boolean DEFAULT false,
  created_at  timestamptz DEFAULT now()
);
CREATE INDEX ON llm_calls (incident_id);
"""


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    op.execute("DROP TABLE llm_calls, actions, approvals, fixes, documents, incidents")
