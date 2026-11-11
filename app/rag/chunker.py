"""문단 경계 기준 청킹.

고정 길이로 자르면 앞부분 한 줄만 고쳐도 뒤쪽 청크 경계가 전부 밀려서 모든 청크의 해시가 바뀐다.
그러면 증분 임베딩의 효과가 사라진다. 제목, 빈 줄(문단) 경계에서 자르면
수정된 문단이 속한 청크만 해시가 바뀐다.
"""
import re

_HEADING = re.compile(r"^#{1,6}\s")


def split_blocks(text: str) -> list[str]:
    blocks, cur = [], []
    for line in text.splitlines():
        if _HEADING.match(line) and cur:
            blocks.append("\n".join(cur).strip())
            cur = []
        if not line.strip():
            if cur:
                blocks.append("\n".join(cur).strip())
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur).strip())
    return [b for b in blocks if b]


def chunk_text(text: str, max_chars: int = 900) -> list[str]:
    chunks, cur = [], ""
    for block in split_blocks(text):
        # 새 제목이 나오면 청크를 끊는다(섹션 단위 유지)
        if cur and (_HEADING.match(block) or len(cur) + len(block) + 2 > max_chars):
            chunks.append(cur)
            cur = ""
        if len(block) > max_chars:
            for i in range(0, len(block), max_chars):
                chunks.append(block[i : i + max_chars])
            continue
        cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        chunks.append(cur)
    return chunks
