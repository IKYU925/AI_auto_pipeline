"""knowledge/ 폴더 문서를 Vector DB에 증분 반영한다. 변경된 청크만 임베딩된다.

실행: python -m scripts.ingest_knowledge [knowledge_dir]
cron이나 CI(문서 저장소 push 시)에서 주기적으로 돌리면 된다.
"""
import json
import sys

from app.container import Container
from app.rag.loader import load_markdown_dir


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "knowledge"
    c = Container()
    report = c.ingestor.ingest(load_markdown_dir(root))
    print(json.dumps({**report.to_dict(), "embedding_cache": c.embedder.stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
