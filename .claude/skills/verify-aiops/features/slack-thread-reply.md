# Slack 스레드 회신

shopapi에서 처리하지 못한 예외가 나면 `#alert` 채널에 장애 알림이 올라오고, 30초 안팎에 같은 스레드에 자동화의 분석과 검증된 수정안이 회신된다.

## Sub-features

- `slack-alert`: shopapi가 Incoming Webhook으로 `[shopapi] critical: ...`와 스택트레이스를 한 메시지(3,500자 이하)로 올린다.
- `slack-intake`: Slack Events API가 n8n으로 메시지를 보내고, 파서가 장애 형식만 골라 자동화 API로 넘긴다. 스레드 댓글과 일반 대화는 무시한다.
- `slack-reply`: 워커가 봇 토큰으로 알림의 스레드에 결과를 회신한다.

## How to get to it (user POV)

- 운영자가 `#alert`에서 알림과 스레드 회신을 본다.
- shopapi(사용 중인 스택, 8100)에 장애를 일으키는 요청을 보낸다.

## Driving it with verify-aiops

Preconditions:

- 이 기능은 격리된 aiops-verify 스택으로는 검증할 수 없다. 사용 중인 스택(8000, 8100, n8n 5678)과 실제 Slack 워크스페이스를 쓰므로, 운영자가 허락한 경우에만 진행한다. 허락이 없으면 이 기능은 "건너뜀, 사용 중인 스택과 실제 Slack 필요"로 보고한다.
- ngrok이 5678을 공개하고 있고, `.env`의 `N8N_WEBHOOK_URL`이 그 주소이며, `python -m scripts.setup_n8n`이 출력한 Request URL이 Slack 앱에 Verified로 들어가 있다.
- Slack 앱의 Socket Mode가 꺼져 있다.
- 비용을 쓰지 않으려면 사용 중인 스택의 `.env`에서 `LIGHT_MODEL=fake:light`, `HEAVY_MODEL=fake:heavy`로 바꾸고 api, worker를 다시 만든다(끝나면 되돌림). 실제 LLM이면 건당 약 $0.08이다.

- **이벤트 도달.** `#alert`에 사람이 아무 메시지나 쓴다. `curl -s "localhost:4040/api/requests/http?limit=1"`의 요청 본문 `type`이 `event_callback`이고 응답이 200.
- **알림부터 회신까지.** `.venv/bin/python -m scripts.slack_e2e S01 --out results/verify/<RUN_ID>/slack.csv`. 행의 `alert_chars`가 4,000 미만, `alert_to_reply_sec`가 기록되고, `outcome_title`이 결과 제목(가짜 LLM이면 에스컬레이션, 실제 LLM이면 수정안 검증 완료).
- **증명.** slack.csv와 Slack 스레드(알림과 회신) 캡처. 스크립트가 버그를 원복하므로 `git status --short targets`가 비어 있다.

## Gotchas

- Slack 앱의 Socket Mode가 켜져 있으면 Request URL이 Verified여도 이벤트가 HTTP로 오지 않는다.
- ngrok 무료 주소는 재시작하면 바뀐다. 바뀌면 `N8N_WEBHOOK_URL`, `setup_n8n`, Slack Request URL을 모두 다시 맞춘다.
- n8n은 재시작 직후 몇 초 동안 웹훅이 등록되지 않아 404를 돌려준다.
- 같은 에러는 중복 제거 기간(600초) 동안 다시 처리되지 않는다. 연속으로 확인할 때는 `dedupe:*` 키를 지우거나 다른 시나리오를 쓴다.
- 회신 전에 버그를 원복하면 워커가 수정안을 검증할 대상이 사라진다. `slack_e2e`는 회신을 받은 뒤 원복한다.
