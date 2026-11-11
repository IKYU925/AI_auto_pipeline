"""에러 지문(fingerprint).

같은 종류의 에러는 숫자, ID, 시간만 다르고 내용이 같다. 이런 값을 지운 뒤 해시를 만들면
1) 짧은 시간에 반복되는 알림을 중복 제거하고
2) 에러 분류 결과(LLM)를 캐시 키로 재사용할 수 있다.
"""
import hashlib
import re

_PATTERNS = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?\b"), "<ts>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hash>"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "<n>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<str>"),
    (re.compile(r"\s+"), " "),
]


def normalize(text: str) -> str:
    out = text.strip()
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out.lower()


def _stack_frames(stacktrace: str | None, limit: int = 5) -> list[str]:
    """스택트레이스에서 파일명과 함수명만 뽑는다(줄 번호는 배포마다 바뀌므로 제외)."""
    if not stacktrace:
        return []
    frames = re.findall(r'File "([^"]+)", line \d+, in (\S+)', stacktrace)
    return [f"{path.rsplit('/', 1)[-1]}:{func}" for path, func in frames[-limit:]]


def fingerprint(service: str, message: str, stacktrace: str | None = None) -> str:
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    basis = "|".join([service.lower(), normalize(first_line), *_stack_frames(stacktrace)])
    return hashlib.sha256(basis.encode()).hexdigest()[:24]
