# F08. Slack 실연동

상태: 완료
관련: plan.md 5장, 1-4

## 문제

1단계 측정은 실험 스크립트가 API로 장애를 접수했다. 실제 운영에서는 서비스가 Slack에 장애 알림을 올리고, 자동화가 그 알림을 받아 처리한 뒤 같은 스레드에 결과를 회신해야 한다. 이 경로가 실제 Slack에서 끝까지 이어지는지 아직 확인하지 않았다.

## 목표와 비목표

- 목표: shopapi 예외가 Slack 채널 알림이 되고, n8n이 받아 자동화로 넘기고, 워커가 그 알림 스레드에 결과를 회신한다.
- 비목표: 측정(1단계 본 실험은 API 경로로 끝났다), 승인 버튼(F06), 고객사별 채널(F05).

## 운영자 시나리오

1. shopapi에서 500이 나면 `#aiops-test` 채널에 `[shopapi] critical: ZeroDivisionError ...`와 스택트레이스가 올라온다.
2. 1분 안에 그 메시지의 스레드에 자동화의 결과(분류, 원인, 수정 패치, 테스트 결과, 비용)가 달린다.
3. 같은 에러가 반복돼 알림이 여러 개 올라와도 처리는 한 번이다.

## 요구사항

- shopapi: `SLACK_WEBHOOK_URL`(compose에서는 `SHOPAPI_SLACK_WEBHOOK_URL`)이 있으면 처리하지 않은 예외를 Incoming Webhook으로 보낸다(이미 있음).
- n8n: Slack Trigger(채널 메시지) → 파서 → `POST /api/v1/incidents`. 파서는 `[서비스] 심각도: 메시지` 형식만 통과시키고, 스레드 댓글(봇 회신 포함)은 무시하고, Slack이 바꿔 보낸 `&lt; &gt; &amp;`를 원래 문자로 되돌린다.
- n8n 공개 주소: Slack Events API가 n8n에 도달하도록 ngrok으로 5678을 공개하고 `N8N_WEBHOOK_URL`에 넣는다.
- 워커: `SLACK_BOT_TOKEN`(chat:write)으로 장애의 `channel`, `thread_ts`에 회신한다(이미 있음).

## 완료 기준

- [x] 파서가 shopapi 알림 형식, 스레드 댓글, 일반 대화, HTML 엔티티를 올바르게 처리한다(node로 실행하는 테스트)
- [x] n8n에 워크플로를 가져올 수 있다(`n8n import:workflow`), n8n 컨테이너에서 api로 토큰 인증 요청이 닿는다. 활성화는 Slack 자격 증명을 연결한 뒤 가능
- [x] 가짜 LLM으로 Slack 알림 1건이 스레드 회신까지 이어진다(비용 없음)
- [x] 실제 LLM으로 Slack 알림 3건이 스레드 회신까지 이어진다(S01, S02, S05, 25~31초, $0.24, results/phase1_slack.md)

## 설계 메모

- 회신이 다시 장애로 들어오는 반복은 파서의 스레드 댓글 무시로 막는다. 워커는 항상 스레드에만 회신한다.
- Incoming Webhook 메시지는 Slack 이벤트에서 봇 메시지(`subtype: bot_message`)로 온다. 파서는 subtype으로 거르지 않는다. 장애 알림 자체가 봇 메시지이기 때문이다.

## Slack 앱 설정

1. 봇 권한: `chat:write`, `channels:history`, `channels:read`. 설치 후 봇 토큰을 `SLACK_BOT_TOKEN`에.
2. Incoming Webhooks를 켜고 알림 채널의 URL을 `SHOPAPI_SLACK_WEBHOOK_URL`에. 봇을 그 채널에 초대하고 채널 ID를 `SLACK_CHANNEL_ID`에.
3. Basic Information의 Signing Secret을 `SLACK_SIGNING_SECRET`에.
4. ngrok으로 5678을 공개하고 주소를 `N8N_WEBHOOK_URL`에. n8n을 다시 만들고 `python -m scripts.setup_n8n`을 실행하면 Request URL이 출력된다.
5. Event Subscriptions: Enable Events On, Request URL 입력(Verified), Subscribe to bot events에 `message.channels`, Save Changes, 재설치.
6. **Socket Mode는 꺼야 한다.** 켜져 있으면 Request URL이 Verified여도 이벤트가 HTTP로 오지 않는다.

## 열린 질문

- ngrok 무료 주소는 재시작하면 바뀐다. 바뀌면 `N8N_WEBHOOK_URL`, Request URL을 다시 넣어야 한다. 3단계에서는 Alertmanager 웹훅이 입구가 되어 이 의존이 없어진다.

## 변경 이력

- 2026-11-14 Slack이 4,000자 넘는 알림을 나눠 올려 스택트레이스가 끊기는 것을 실제 채널에서 확인하고 shopapi 알림을 3,500자로 제한했다.
- 2026-11-14 Incoming Webhook 봇 메시지도 이벤트로 온다는 것을 확인해 열린 질문을 닫았다. Socket Mode가 켜져 있으면 이벤트가 오지 않는 것을 설정 절차에 적었다.
- 2026-11-14 Slack이 `< > &`를 엔티티로 바꿔 보내 스택트레이스가 깨지는 것을 파서 테스트로 확인하고 고쳤다.
