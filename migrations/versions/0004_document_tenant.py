"""지식 문서에 고객사를 남겨 런북 검색이 다른 고객사 문서를 가져오지 않게 한다(plan.md 9-7).

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute("ALTER TABLE documents ADD COLUMN tenant text NOT NULL DEFAULT 'common'")
    op.execute("CREATE INDEX ON documents (tenant)")


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP COLUMN tenant")
