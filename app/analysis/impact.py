"""영향 범위 분석.

저장소의 import 그래프를 만들고, 바뀐 모듈을 import하는 모듈을 역방향으로 따라간다.
1단계(직접 import하는 모듈)는 바뀐 심볼 이름을 실제로 참조하는 모듈만 남겨 범위를 줄이고,
2단계부터는 동작 변경이 전파될 수 있으므로 모두 포함한다.
"""
import ast
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from app.analysis.diff_analyzer import DiffAnalysis

EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "build", "dist", ".tox", ".pytest_cache"}


def is_test_path(rel: str) -> bool:
    name = Path(rel).name
    return name.startswith("test_") or name.endswith("_test.py") or "tests" in Path(rel).parts


@dataclass
class ModuleInfo:
    name: str
    path: str
    is_test: bool
    imports: set[str] = field(default_factory=set)       # 내부 모듈만
    identifiers: set[str] = field(default_factory=set)   # 이 모듈이 쓰는 이름(Name, Attribute, import 별칭)


class ImportGraph:
    def __init__(self, repo_root: str):
        self.repo = Path(repo_root).resolve()
        self.modules: dict[str, ModuleInfo] = {}
        self.by_path: dict[str, str] = {}
        self._scan()
        self.reverse: dict[str, set[str]] = {m: set() for m in self.modules}
        for m, info in self.modules.items():
            for dep in info.imports:
                self.reverse.setdefault(dep, set()).add(m)

    def _module_name(self, rel: Path) -> str:
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def _scan(self) -> None:
        raw: dict[str, tuple[Path, ast.Module]] = {}
        for path in self.repo.rglob("*.py"):
            rel = path.relative_to(self.repo)
            if any(p in EXCLUDE_DIRS for p in rel.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            name = self._module_name(rel)
            raw[name] = (rel, tree)
            self.modules[name] = ModuleInfo(name=name, path=str(rel), is_test=is_test_path(str(rel)))
            self.by_path[str(rel)] = name

        for name, (rel, tree) in raw.items():
            info = self.modules[name]
            is_pkg = rel.name == "__init__.py"
            pkg_parts = name.split(".") if is_pkg else name.split(".")[:-1]
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self._add_import(info, a.name)
                        info.identifiers.add((a.asname or a.name).split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    base = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level else []
                    mod = ".".join([*base, node.module] if node.module else base)
                    for a in node.names:
                        # from pkg import submodule 형태도 처리
                        if not self._add_import(info, f"{mod}.{a.name}" if mod else a.name):
                            self._add_import(info, mod)
                        info.identifiers.add(a.name)
                        if a.asname:
                            info.identifiers.add(a.asname)
                elif isinstance(node, ast.Name):
                    info.identifiers.add(node.id)
                elif isinstance(node, ast.Attribute):
                    info.identifiers.add(node.attr)

    def _add_import(self, info: ModuleInfo, target: str) -> bool:
        parts = target.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in self.modules and cand != info.name:
                info.imports.add(cand)
                return True
        return False

    def module_of(self, rel_path: str) -> str | None:
        return self.by_path.get(rel_path)

    def test_modules(self) -> list[str]:
        return [m for m, info in self.modules.items() if info.is_test]


@dataclass
class ImpactReport:
    changed_modules: list[str]
    impacted: dict[str, int]          # module -> 영향 깊이(1 = 직접 import)
    non_python_changes: list[str]
    unknown_files: list[str]
    risk: str
    reasons: list[str]

    @property
    def impacted_source_modules(self) -> list[str]:
        return [m for m in self.impacted if not is_test_path(m.replace(".", "/") + ".py")]

    def to_dict(self) -> dict:
        return {
            "changed_modules": self.changed_modules,
            "impacted": self.impacted,
            "non_python_changes": self.non_python_changes,
            "unknown_files": self.unknown_files,
            "risk": self.risk,
            "reasons": self.reasons,
        }


def _references(info: ModuleInfo, symbols: list[str]) -> bool:
    for s in symbols:
        parts = s.split(".")
        if parts[0] in info.identifiers or parts[-1] in info.identifiers:
            return True
    return False


def analyze_impact(diff: DiffAnalysis, graph: ImportGraph, max_depth: int = 10) -> ImpactReport:
    changed, non_py, unknown, reasons = [], [], [], []
    symbols_by_module: dict[str, list[str]] = {}
    sig_changed = False

    for f in diff.files:
        if not f.path.endswith(".py"):
            non_py.append(f.path)
            continue
        mod = graph.module_of(f.old_path or f.path) or graph.module_of(f.path)
        if mod is None:
            unknown.append(f.path)
            continue
        changed.append(mod)
        symbols_by_module[mod] = f.changed_symbols
        if f.signature_changed:
            sig_changed = True
            reasons.append(f"{f.path}: 시그니처 변경 {f.signature_changed}")

    impacted: dict[str, int] = {}
    queue = deque()
    for mod in changed:
        symbols = symbols_by_module.get(mod) or []
        for importer in graph.reverse.get(mod, ()):
            info = graph.modules[importer]
            # 모듈 레벨 변경(심볼을 못 찾은 경우)이면 직접 import하는 모듈 전부, 아니면 심볼 참조 모듈만
            if not symbols or _references(info, symbols):
                if importer not in impacted and importer not in changed:
                    impacted[importer] = 1
                    queue.append(importer)
    while queue:
        mod = queue.popleft()
        depth = impacted[mod]
        if depth >= max_depth:
            continue
        for importer in graph.reverse.get(mod, ()):
            if importer not in impacted and importer not in changed:
                impacted[importer] = depth + 1
                queue.append(importer)

    src_impacted = [m for m in impacted if not graph.modules[m].is_test]
    if non_py:
        reasons.append(f"코드 외 파일 변경: {non_py}")
    if any(m.endswith("conftest") for m in changed):
        reasons.append("conftest 변경: 전체 테스트 영향")
    if sig_changed or non_py or len(src_impacted) > 10:
        risk = "high"
    elif len(src_impacted) > 3:
        risk = "medium"
    else:
        risk = "low"
    reasons.append(f"변경 모듈 {len(changed)}개, 영향 받는 소스 모듈 {len(src_impacted)}개")
    return ImpactReport(changed, impacted, non_py, unknown, risk, reasons)
