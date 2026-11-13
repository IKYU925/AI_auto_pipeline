"""LangGraph 워크플로.

START -> classify(LIGHT) -> retrieve_context(RAG)
  -> [코드 수정 불필요] report
  -> lookup_known_fix
       -> [검증된 수정안 있음] analyze_diff (LLM 호출 없이 재검증)
       -> [없음] analyze_root_cause(HEAVY) -> generate_fix(HEAVY) -> analyze_diff
analyze_diff -> [패치 형식 오류] reject_invalid_patch -> generate_fix
             -> analyze_impact(영향 범위 + 테스트 선별) -> run_tests
run_tests -> [통과] report
          -> [실패 & 시도 남음] generate_fix (실패 로그를 붙여 재생성 = 회귀 테스트 루프)
          -> [실패 & 시도 소진] escalate -> report
예산 초과 시 어느 LLM 노드에서든 report로 빠진다.
"""
from langgraph.graph import END, START, StateGraph

from app.graph.nodes import Nodes
from app.graph.state import IncidentState


def _stopped(state: IncidentState) -> bool:
    return state.get("outcome") == "budget_exceeded"


def build_graph(nodes: Nodes, max_attempts: int, early_exit: bool = True):
    g = StateGraph(IncidentState)
    g.add_node("classify", nodes.classify)
    g.add_node("retrieve_context", nodes.retrieve_context)
    g.add_node("lookup_known_fix", nodes.lookup_known_fix)
    g.add_node("analyze_root_cause", nodes.analyze_root_cause)
    g.add_node("generate_fix", nodes.generate_fix)
    g.add_node("analyze_diff", nodes.analyze_diff)
    g.add_node("reject_invalid_patch", nodes.reject_invalid_patch)
    g.add_node("analyze_impact", nodes.analyze_impact)
    g.add_node("run_tests", nodes.run_tests)
    g.add_node("escalate", nodes.escalate)
    g.add_node("report", nodes.report)

    g.add_edge(START, "classify")
    g.add_conditional_edges(
        "classify", lambda s: "report" if _stopped(s) else "retrieve_context", ["report", "retrieve_context"]
    )

    def needs_fix(s: IncidentState) -> bool:
        cls = s["classification"]
        return cls["needs_code_fix"] and cls["confidence"] >= 0.5

    def after_retrieve(s: IncidentState) -> str:
        if needs_fix(s):
            return "lookup_known_fix"
        # 코드 수정이 필요 없는 장애(인프라, 설정)는 고성능 모델을 부르지 않고 런북 안내로 끝낸다.
        # 조기 종료를 끄면(실험 조건 B) 원인 분석까지 실행한 뒤 끝낸다
        return "report" if early_exit else "analyze_root_cause"

    g.add_conditional_edges("retrieve_context", after_retrieve, ["report", "lookup_known_fix", "analyze_root_cause"])
    g.add_conditional_edges(
        "lookup_known_fix",
        lambda s: "analyze_diff" if s.get("reused_fix") else "analyze_root_cause",
        ["analyze_diff", "analyze_root_cause"],
    )
    g.add_conditional_edges(
        "analyze_root_cause",
        lambda s: "report" if _stopped(s) or not needs_fix(s) else "generate_fix",
        ["report", "generate_fix"],
    )
    g.add_conditional_edges(
        "generate_fix", lambda s: "report" if _stopped(s) else "analyze_diff", ["report", "analyze_diff"]
    )

    def after_diff(s: IncidentState) -> str:
        if s["diff_analysis"]["files"]:
            return "analyze_impact"
        return "reject_invalid_patch" if s.get("attempts", 0) < max_attempts else "escalate"

    g.add_conditional_edges("analyze_diff", after_diff, ["analyze_impact", "reject_invalid_patch", "escalate"])
    g.add_edge("reject_invalid_patch", "generate_fix")
    g.add_edge("analyze_impact", "run_tests")

    def after_tests(s: IncidentState) -> str:
        if s["test_result"]["passed"]:
            return "report"
        if s.get("attempts", 0) >= max_attempts:
            return "escalate"
        # 재사용한 수정안이 실패했다면 원인 분석부터 다시 한다
        return "generate_fix" if s.get("root_cause") else "analyze_root_cause"

    g.add_conditional_edges("run_tests", after_tests, ["report", "generate_fix", "analyze_root_cause", "escalate"])
    g.add_edge("escalate", "report")
    g.add_edge("report", END)
    return g.compile()


def recursion_limit(max_attempts: int) -> int:
    return 15 + max_attempts * 6
