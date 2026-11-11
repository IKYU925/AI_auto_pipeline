"""프롬프트 템플릿. 프롬프트에 넣는 모든 입력은 길이 상한을 둬서 토큰 비용을 통제한다."""


def clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = limit // 3
    return text[:head] + "\n...[생략]...\n" + text[-(limit - head) :]


CLASSIFY_SYSTEM = """너는 장애 알림을 분류하는 SRE 보조 도구다.
알림 내용만 보고 장애 유형을 분류하고, 애플리케이션 코드 수정이 필요한지 판단한다.
- code_bug: 예외, 로직 오류처럼 코드 수정이 필요한 경우
- config, infra, dependency, data: 설정, 인프라, 외부 의존성, 데이터 문제
확신이 낮으면 confidence를 낮게 준다. keywords에는 런북 검색에 쓸 단어를 3~6개 넣는다."""


def classify_user(service: str, message: str, stacktrace: str | None) -> str:
    return f"""[서비스] {service}
[알림]
{clip(message, 1500)}
[스택트레이스 마지막 부분]
{clip(stacktrace, 1500)[-1500:]}"""


ROOT_CAUSE_SYSTEM = """너는 시니어 백엔드 엔지니어다. 장애 정보, 관련 코드, 사내 문서(런북, 포스트모템)를 근거로
근본 원인을 추론한다. 추측이면 추측이라고 쓰고, evidence에는 근거가 된 코드 위치나 문서를 적는다.
suspected_files에는 실제로 수정이 필요할 가능성이 높은 파일만 저장소 루트 기준 상대 경로로 넣는다."""


def root_cause_user(incident: dict, classification: dict, code_context: str, docs: str) -> str:
    return f"""[장애]
서비스: {incident['service']}
메시지: {clip(incident['message'], 1500)}
스택트레이스:
{clip(incident.get('stacktrace'), 3000)}

[분류] {classification['category']} - {classification['summary']}

[관련 코드]
{code_context or '(스택트레이스에서 저장소 내부 파일을 찾지 못함)'}

[관련 사내 문서]
{docs or '(검색 결과 없음)'}"""


FIX_SYSTEM = """너는 장애를 고치는 패치를 작성한다. 규칙:
1. 출력 patch는 저장소 루트 기준 unified diff다. 헤더는 '--- a/경로', '+++ b/경로'를 쓴다.
2. 원인을 고치는 최소 변경만 한다. 리팩터링, 포매팅 변경, 무관한 파일 수정은 하지 않는다.
3. 기존 함수의 시그니처와 정상 경로의 동작은 바꾸지 않는다(회귀 방지).
4. 이전 시도가 실패했다면 그 실패 로그를 읽고 같은 실수를 반복하지 않는다.
5. 컨텍스트 줄은 제공된 코드와 정확히 같아야 git apply가 성공한다. 코드 앞의 줄 번호 표시('  12| ')는 패치에 넣지 않는다."""


def fix_user(incident: dict, root_cause: dict, code_context: str, history: list[dict]) -> str:
    prev = ""
    for i, h in enumerate(history, 1):
        prev += f"\n--- 이전 시도 {i} (실패) ---\n[설명] {h.get('explanation', '')}\n[패치]\n{clip(h.get('patch'), 1500)}\n[실패 원인]\n{h.get('failure', '')}\n"
    return f"""[장애] {incident['service']}: {clip(incident['message'], 800)}

[근본 원인] {root_cause['root_cause']}
[수정 방향] {root_cause['fix_strategy']}

[수정 대상 코드]
{code_context}
{prev}"""
