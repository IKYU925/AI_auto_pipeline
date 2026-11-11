"""knowledge/ 아래 마크다운 문서를 읽는다. 문서 맨 위 front matter가 메타데이터가 된다.

---
service: checkout
category: code_bug
doc_type: runbook
updated_at: 2026-10-01
---
"""
from datetime import datetime
from pathlib import Path

from app.rag.ingest import Document


def parse_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    meta = {}
    for line in fm.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body.strip()


def load_markdown_dir(root: str) -> list[Document]:
    docs = []
    base = Path(root)
    for path in sorted(base.rglob("*.md")):
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        updated = meta.get("updated_at")
        docs.append(
            Document(
                doc_id=str(path.relative_to(base)),
                text=body,
                service=meta.get("service", "common"),
                category=meta.get("category", "general"),
                doc_type=meta.get("doc_type", "runbook"),
                updated_at=datetime.fromisoformat(updated) if updated else None,
                source_uri=str(path),
            )
        )
    return docs
