"""Diff 분석: 어떤 파일의 어떤 줄이 추가, 삭제되었고 어떤 함수, 클래스가 바뀌었는지 뽑는다.

LLM이 만든 패치는 아직 저장소에 적용되지 않은 상태이므로, 원본 파일(old)의 AST와
hunk의 old 줄 번호를 대조해서 변경된 심볼을 찾는다.
- 삭제된 줄: old 파일의 해당 줄 번호
- 추가된 줄: old 파일에서 삽입 위치의 앞뒤 줄 번호
- 새로 추가된 함수: 추가된 줄의 'def 이름(' 패턴
"""
import ast
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)")


@dataclass
class FileChange:
    path: str
    old_path: str | None
    status: str  # added | modified | deleted | renamed
    added: int = 0
    removed: int = 0
    touched_old_lines: list[int] = field(default_factory=list)
    added_defs: list[str] = field(default_factory=list)
    removed_defs: list[str] = field(default_factory=list)
    changed_symbols: list[str] = field(default_factory=list)
    signature_changed: list[str] = field(default_factory=list)


@dataclass
class DiffAnalysis:
    files: list[FileChange]

    @property
    def total_added(self) -> int:
        return sum(f.added for f in self.files)

    @property
    def total_removed(self) -> int:
        return sum(f.removed for f in self.files)

    @property
    def changed_paths(self) -> list[str]:
        return [f.path for f in self.files]

    def to_dict(self) -> dict:
        return {
            "files": [asdict(f) for f in self.files],
            "total_added": self.total_added,
            "total_removed": self.total_removed,
        }


def _strip_prefix(p: str) -> str | None:
    p = p.strip().split("\t")[0]
    if p == "/dev/null":
        return None
    return p[2:] if p.startswith(("a/", "b/")) else p


def parse_unified_diff(patch: str) -> list[FileChange]:
    files: list[FileChange] = []
    cur: FileChange | None = None
    old_ln = new_ln = 0
    lines = patch.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old_p, new_p = _strip_prefix(line[4:]), _strip_prefix(lines[i + 1][4:])
            status = "added" if old_p is None else "deleted" if new_p is None else (
                "renamed" if old_p != new_p else "modified"
            )
            cur = FileChange(path=new_p or old_p or "", old_path=old_p, status=status)
            files.append(cur)
            i += 2
            continue
        m = _HUNK.match(line)
        if m and cur:
            old_ln, new_ln = int(m.group(1)), int(m.group(3))
        elif cur and line.startswith("+"):
            cur.added += 1
            cur.touched_old_lines.extend([max(old_ln - 1, 1), old_ln])  # 삽입 위치 앞뒤
            d = _DEF.match(line[1:])
            if d:
                cur.added_defs.append(d.group(1))
            new_ln += 1
        elif cur and line.startswith("-"):
            cur.removed += 1
            cur.touched_old_lines.append(old_ln)
            d = _DEF.match(line[1:])
            if d:
                cur.removed_defs.append(d.group(1))
            old_ln += 1
        elif cur and (line.startswith(" ") or line == ""):
            old_ln += 1
            new_ln += 1
        i += 1
    for f in files:
        f.touched_old_lines = sorted(set(f.touched_old_lines))
    return files


def split_patch_by_file(patch: str) -> list[str]:
    """파일별 패치로 나눈다.

    LLM은 hunk 헤더의 줄 수를 자주 틀리게 쓰기 때문에 git apply --recount로 다시 세게 하는데,
    여러 파일이 한 패치에 있으면 recount가 다음 파일의 '--- a/...' 줄까지 삭제 줄로 세어 버린다.
    파일 단위로 나눠서 각각 적용하면 이 문제가 없다.
    """
    lines = patch.splitlines()
    starts = [
        i for i in range(len(lines) - 1)
        if lines[i].startswith("--- ") and lines[i + 1].startswith("+++ ")
    ]
    parts = []
    for n, s in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        # diff --git, index 같은 머리줄은 앞 파일 끝에 붙어 있으므로 잘라낸다
        body = lines[s:end]
        while body and body[-1].startswith(("diff --git", "index ", "new file mode", "deleted file mode")):
            body.pop()
        parts.append("\n".join(body) + "\n")
    return parts


def _symbol_spans(source: str) -> list[tuple[str, int, int]]:
    """(qualified_name, start, end). 메서드는 Class.method 형태."""
    spans = []

    def visit(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                start = min([d.lineno for d in child.decorator_list] + [child.lineno])
                spans.append((name, start, child.end_lineno or child.lineno))
                visit(child, f"{name}.")

    visit(ast.parse(source))
    return spans


def analyze_diff(patch: str, repo_root: str) -> DiffAnalysis:
    files = parse_unified_diff(patch)
    repo = Path(repo_root)
    for f in files:
        if not f.path.endswith(".py") or f.old_path is None:
            f.changed_symbols = sorted(set(f.added_defs))
            continue
        src_path = repo / f.old_path
        if not src_path.is_file():
            continue
        try:
            spans = _symbol_spans(src_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        touched = set(f.touched_old_lines)
        symbols = set()
        for name, start, end in spans:
            if any(start <= ln <= end for ln in touched):
                # 가장 안쪽 심볼을 기록하기 위해 클래스 자체보다 메서드를 우선한다
                symbols.add(name)
        # 클래스 안 메서드가 잡혔으면 클래스 이름 자체는 제외(영향 범위 과대 추정 방지)
        symbols = {s for s in symbols if not any(o.startswith(s + ".") for o in symbols)}
        symbols |= set(f.added_defs)
        f.changed_symbols = sorted(symbols)
        # def 줄이 삭제됐다면 시그니처가 바뀌었거나 함수가 없어진 것 -> 호출부 전체가 영향 범위
        f.signature_changed = sorted(set(f.removed_defs))
    return DiffAnalysis(files=files)
