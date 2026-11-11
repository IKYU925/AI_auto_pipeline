"""LLM에 넘길 코드 컨텍스트 수집.

저장소 전체를 프롬프트에 넣으면 비용이 폭증한다. 스택트레이스에 찍힌 위치 주변과
의심 파일만 줄 번호와 함께 잘라서 넣는다.
"""
import re
from pathlib import Path

_FRAME = re.compile(r'File "([^"]+)", line (\d+)')


def _resolve(repo: Path, raw_path: str) -> Path | None:
    """운영 서버 경로(/app/shop/pricing.py)를 저장소 상대 경로로 맞춘다."""
    parts = Path(raw_path).parts
    for i in range(len(parts)):
        candidate = repo.joinpath(*parts[i:])
        if candidate.is_file():
            return candidate
    return None


def _window(path: Path, repo: Path, center: int | None, radius: int, full_max_lines: int) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if center is None or len(lines) <= full_max_lines:
        start, end = 1, len(lines)
    else:
        start, end = max(1, center - radius), min(len(lines), center + radius)
    body = "\n".join(f"{n:>4}| {lines[n - 1]}" for n in range(start, end + 1))
    return f"### {path.relative_to(repo)} (lines {start}-{end})\n{body}"


def from_stacktrace(repo_root: str, stacktrace: str | None, radius: int = 25, max_files: int = 4) -> str:
    if not stacktrace:
        return ""
    repo = Path(repo_root).resolve()
    seen, blocks = set(), []
    # 가장 안쪽(마지막) 프레임이 원인에 가깝기 때문에 뒤에서부터 본다
    for raw, line in reversed(_FRAME.findall(stacktrace)):
        path = _resolve(repo, raw)
        if not path or path in seen:
            continue
        seen.add(path)
        blocks.append(_window(path, repo, int(line), radius, full_max_lines=120))
        if len(blocks) >= max_files:
            break
    return "\n\n".join(blocks)


def from_files(repo_root: str, files: list[str], full_max_lines: int = 300) -> str:
    repo = Path(repo_root).resolve()
    blocks = []
    for f in files:
        path = _resolve(repo, f)
        if path:
            # 수정 대상 파일은 패치 컨텍스트가 정확해야 하므로 가능하면 전체를 준다
            blocks.append(_window(path, repo, None, 0, full_max_lines))
    return "\n\n".join(blocks)
