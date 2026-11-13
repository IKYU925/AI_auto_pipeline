"""LangGraph 노드 구현. 각 노드는 상태의 일부만 갱신해서 돌려준다."""
import functools
import json
import logging
from pathlib import Path

from app.analysis import code_context
from app.analysis.diff_analyzer import analyze_diff
from app.analysis.impact import ImportGraph, analyze_impact
from app.analysis.test_selector import TestSelection, select_tests
from app.config import Settings
from app.core.fingerprint import normalize
from app.graph.state import IncidentState
from app.llm import prompts
from app.llm.cost import BudgetExceeded, CostTracker
from app.llm.router import LLMRouter
from app.llm.schemas import Classification, FixProposal, RootCause
from app.rag.retriever import Retriever, SearchFilter
from app.testing.runner import SandboxTestRunner

log = logging.getLogger(__name__)


def budget_guard(fn):
    """예산 초과 시 그래프를 멈추고 보고 단계로 보낸다."""

    @functools.wraps(fn)
    def wrapper(self, state: IncidentState) -> dict:
        try:
            return fn(self, state)
        except BudgetExceeded as e:
            log.warning("budget exceeded: %s", e)
            return {"outcome": "budget_exceeded", "error": str(e)}

    return wrapper


class Nodes:
    def __init__(self, settings: Settings, router: LLMRouter, retriever: Retriever, cost: CostTracker, redis):
        self.s = settings
        self.redis = redis
        self.router = router
        self.retriever = retriever
        self.cost = cost
        self.runner = SandboxTestRunner(settings.target_repo_path, settings.test_timeout_sec, settings.failure_log_max_chars)

    # 1. 에러 분류 (LIGHT 모델, 지문 기반 캐시)
    @budget_guard
    def classify(self, state: IncidentState) -> dict:
        inc = state["incident"]
        result = self.router.invoke(
            "classify",
            Classification,
            prompts.CLASSIFY_SYSTEM,
            prompts.classify_user(inc["service"], inc["message"], inc.get("stacktrace")),
            incident_id=state["incident_id"],
            cache_key=state["fingerprint"],
        )
        return {"classification": result.model_dump()}

    # 2. RAG: 메타데이터로 범위를 좁힌 뒤 유사도 검색
    def retrieve_context(self, state: IncidentState) -> dict:
        inc, cls = state["incident"], state["classification"]
        # 질의에서 주문번호, 시간 같은 가변 값을 지워야 같은 에러가 재발했을 때 검색 캐시가 적중한다
        query = " ".join([cls["summary"], *cls.get("keywords", []), normalize(inc["message"].splitlines()[0][:200])])
        flt = SearchFilter(
            service=inc["service"],
            categories=[cls["category"], "general"],
            since_days=self.s.retrieval_lookback_days,
        )
        hits = self.retriever.search(query, flt, top_k=self.s.retrieval_top_k)
        docs = [
            {"doc_id": h.doc_id, "score": h.score, "doc_type": h.doc_type, "text": h.text[: self.s.context_chunk_max_chars]}
            for h in hits
        ]
        return {"context_docs": docs}

    # 검증까지 끝난 수정안이 있으면(같은 에러가 배포 전에 재발한 경우) LLM 없이 재검증만 한다
    def lookup_known_fix(self, state: IncidentState) -> dict:
        if not self.s.opt_fix_reuse:
            return {}
        raw = self.redis.get(f"fix:resolved:{state['fingerprint']}")
        if not raw:
            return {}
        return {"fix": json.loads(raw), "reused_fix": True}

    # 3. 원인 분석 (HEAVY 모델)
    @budget_guard
    def analyze_root_cause(self, state: IncidentState) -> dict:
        inc = state["incident"]
        code = code_context.from_stacktrace(self.s.target_repo_path, inc.get("stacktrace"))
        docs = "\n\n".join(f"[{d['doc_type']}:{d['doc_id']}]\n{d['text']}" for d in state.get("context_docs", []))
        result = self.router.invoke(
            "root_cause",
            RootCause,
            prompts.ROOT_CAUSE_SYSTEM,
            prompts.root_cause_user(inc, state["classification"], code, docs),
            incident_id=state["incident_id"],
        )
        return {"root_cause": result.model_dump()}

    # 4. 수정 코드 생성 (HEAVY 모델). 재시도 시 이전 실패 로그를 함께 보낸다
    @budget_guard
    def generate_fix(self, state: IncidentState) -> dict:
        rc = state["root_cause"]
        files = rc.get("suspected_files") or []
        code = code_context.from_files(self.s.target_repo_path, files) or code_context.from_stacktrace(
            self.s.target_repo_path, state["incident"].get("stacktrace")
        )
        result = self.router.invoke(
            "generate_fix",
            FixProposal,
            prompts.FIX_SYSTEM,
            prompts.fix_user(state["incident"], rc, code, state.get("history", [])),
            incident_id=state["incident_id"],
        )
        return {"fix": result.model_dump(), "attempts": state.get("attempts", 0) + 1, "reused_fix": False}

    # 5. Diff 분석
    def analyze_diff(self, state: IncidentState) -> dict:
        analysis = analyze_diff(state["fix"]["patch"], self.s.target_repo_path)
        return {"diff_analysis": analysis.to_dict()}

    # 6. 영향 범위 분석 + 7. 테스트 선별
    def analyze_impact(self, state: IncidentState) -> dict:
        analysis = analyze_diff(state["fix"]["patch"], self.s.target_repo_path)
        graph = ImportGraph(self.s.target_repo_path)
        impact = analyze_impact(analysis, graph)
        if self.s.opt_test_selection:
            selection = select_tests(impact, graph, self.s.fallback_full_suite)
        else:
            selection = TestSelection([], True, "테스트 선별 끔: 전체 테스트 실행", len(graph.test_modules()))
        return {"impact": impact.to_dict(), "test_selection": selection.to_dict()}

    # 8. 테스트 실행
    def run_tests(self, state: IncidentState) -> dict:
        sel = state["test_selection"]
        result = self.runner.run(state["fix"]["patch"], sel["tests"], full_suite=sel["full_suite"])
        update: dict = {"test_result": result.to_dict()}
        if not result.passed:
            history = list(state.get("history", []))
            history.append(
                {
                    "attempt": state.get("attempts", 0),
                    "patch": state["fix"]["patch"],
                    "explanation": state["fix"]["explanation"],
                    "failure": f"{result.summary}\n{result.failure_log}",
                }
            )
            update["history"] = history
        return update

    # 잘못된 패치(파일 0개)는 테스트 단계까지 가지 않고 바로 재시도
    def reject_invalid_patch(self, state: IncidentState) -> dict:
        history = list(state.get("history", []))
        history.append(
            {
                "attempt": state.get("attempts", 0),
                "patch": state["fix"]["patch"],
                "explanation": state["fix"]["explanation"],
                "failure": "unified diff 형식이 아니라서 변경 파일을 하나도 찾지 못했다. '--- a/경로' '+++ b/경로' 헤더와 @@ hunk를 포함해야 한다.",
            }
        )
        return {"history": history, "test_result": {"passed": False, "applied": False, "summary": "invalid patch"}}

    def escalate(self, state: IncidentState) -> dict:
        return {"outcome": "escalated"}

    # 9. 결과 보고서
    def report(self, state: IncidentState) -> dict:
        outcome = state.get("outcome")
        if not outcome:
            if state.get("test_result", {}).get("passed"):
                outcome = "resolved"
            elif state.get("classification") and not state["classification"].get("needs_code_fix"):
                outcome = "no_code_fix"
            else:
                outcome = "escalated"

        patch_path = ""
        if outcome == "resolved":
            out_dir = Path(self.s.artifacts_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            p = out_dir / f"{state['incident_id']}.patch"
            p.write_text(state["fix"]["patch"], encoding="utf-8")
            patch_path = str(p)
            self.redis.set(f"fix:resolved:{state['fingerprint']}", json.dumps(state["fix"]), ex=7 * 86400)

        cost = self.cost.summary(state["incident_id"])
        return {
            "outcome": outcome,
            "patch_path": patch_path,
            "cost": cost,
            "report": render_report({**state, "outcome": outcome, "cost": cost, "patch_path": patch_path}),
        }


OUTCOME_TITLE = {
    "resolved": "수정안 검증 완료 (사람 승인 대기)",
    "escalated": "자동 수정 실패 - 담당자 확인 필요",
    "no_code_fix": "코드 수정 대상 아님 - 런북 안내",
    "budget_exceeded": "LLM 비용 상한 도달 - 담당자 확인 필요",
}


def render_report(state: dict) -> str:
    inc = state["incident"]
    cls = state.get("classification", {})
    lines = [
        f"*[{OUTCOME_TITLE.get(state['outcome'], state['outcome'])}]* `{inc['service']}`",
        f"> {inc['message'].splitlines()[0][:200]}",
    ]
    if cls:
        lines.append(f"*분류*: {cls.get('category')} (신뢰도 {cls.get('confidence', 0):.2f}) - {cls.get('summary')}")
    if state.get("root_cause"):
        lines.append(f"*원인*: {state['root_cause']['root_cause']}")
    if state.get("fix"):
        reused = " [이전에 검증된 수정안 재사용]" if state.get("reused_fix") else ""
        lines.append(f"*수정 내용*: {state['fix']['explanation']} (위험도 {state['fix'].get('risk')}){reused}")
    if state.get("impact"):
        imp = state["impact"]
        lines.append(f"*영향 범위*: 변경 {imp['changed_modules']} / 영향 {list(imp['impacted'])} / 위험도 {imp['risk']}")
    if state.get("test_selection"):
        sel = state["test_selection"]
        lines.append(f"*회귀 테스트*: {sel['reason']}" + (f" {sel['tests']}" if sel["tests"] else ""))
    if state.get("test_result"):
        tr = state["test_result"]
        lines.append(f"*테스트 결과*: {tr.get('summary')} (시도 {state.get('attempts', 0)}회)")
    if state["outcome"] == "no_code_fix" and state.get("context_docs"):
        lines.append("*참고 문서*: " + ", ".join(dict.fromkeys(d["doc_id"] for d in state["context_docs"])))
    if state.get("error"):
        lines.append(f"*중단 사유*: {state['error']}")
    cost = state.get("cost", {})
    lines.append(
        f"*LLM 비용*: ${cost.get('usd', 0):.4f} (입력 {cost.get('tokens_in', 0)} / 출력 {cost.get('tokens_out', 0)} 토큰, "
        f"캐시 적중 {sum(v for k, v in cost.items() if k.startswith('cache_hit:'))}회)"
    )
    if state.get("fix") and state["outcome"] == "resolved":
        lines.append(f"```\n{state['fix']['patch'][:2500]}\n```")
    return "\n".join(lines)
