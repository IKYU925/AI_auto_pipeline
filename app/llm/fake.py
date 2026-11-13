"""API 키 없이 파이프라인을 돌려보기 위한 가짜 LLM.

responder(schema, messages)가 스키마 인스턴스를 돌려주면, LangChain의
with_structured_output(include_raw=True) 결과와 같은 모양으로 감싸서 반환한다.
"""
from collections.abc import Callable

from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel

Responder = Callable[[type[BaseModel], list[BaseMessage]], BaseModel]


class _FakeRunnable:
    def __init__(self, name: str, schema: type[BaseModel], responder: Responder, calls: list):
        self.name, self.schema, self.responder, self.calls = name, schema, responder, calls

    def invoke(self, messages: list[BaseMessage]) -> dict:
        self.calls.append((self.name, self.schema.__name__))
        parsed = self.responder(self.schema, messages)
        prompt_chars = sum(len(str(m.content)) for m in messages)
        raw = AIMessage(
            content="",
            usage_metadata={
                "input_tokens": prompt_chars // 4,
                "output_tokens": len(parsed.model_dump_json()) // 4,
                "total_tokens": prompt_chars // 4 + len(parsed.model_dump_json()) // 4,
            },
        )
        return {"raw": raw, "parsed": parsed, "parsing_error": None}


class FakeChatModel:
    def __init__(self, name: str, responder: Responder, calls: list):
        self.name, self.responder, self.calls = name, responder, calls

    def with_structured_output(self, schema: type[BaseModel], include_raw: bool = False, method: str = ""):
        return _FakeRunnable(self.name, schema, self.responder, self.calls)


def fake_model_factory(responder: Responder, calls: list) -> Callable[[str], FakeChatModel]:
    return lambda name: FakeChatModel(name, responder, calls)
