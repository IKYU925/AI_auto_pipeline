# Day 4 결과: Slack 실연동

실행: 2026-11-14 KST 14시, 실제 LLM(Haiku 5.5, Opus 5.5), 최적화 모두 켬. 원본은 [day4_slack.csv](day4_slack.csv), 재실행은 `python -m scripts.slack_e2e S01 S02 S05 --out results/day4_slack.csv`.

경로: shopapi 500 → Incoming Webhook으로 `#alert`에 알림 → Slack Events API(`message.channels`) → ngrok → n8n Slack Trigger와 파서 → `POST /api/v1/incidents` → 워커 → 봇 토큰으로 알림 스레드에 회신

| 시나리오 | 알림 길이 | 알림부터 회신까지 | 결과 | LLM 비용 |
| --- | --- | --- | --- | --- |
| S01 빈 주문 0 나누기 | 3,466자 | 31.1초 | 수정안 검증 완료(사람 승인 대기) | $0.0834 |
| S02 쿠폰 없음 None 접근 | 3,435자 | 24.9초 | 수정안 검증 완료(사람 승인 대기) | $0.0813 |
| S05 마지막 페이지 IndexError | 3,556자 | 29.2초 | 수정안 검증 완료(사람 승인 대기) | $0.0763 |

알림부터 회신까지 시간은 Slack 메시지 시각(ts)의 차이다. 1단계 API 경로의 처리 시간(약 27초)과 비슷해서, Slack과 n8n을 거치는 지연은 몇 초 수준으로 본다(추정, 구간별로 재지는 않음).

## 연동하며 찾은 문제

- Slack은 4,000자가 넘는 메시지를 둘로 나눠 올렸다(3,987자와 137자). 첫 메시지의 코드 블록이 닫히지 않아 파서가 스택트레이스를 읽지 못한다. shopapi가 알림을 3,500자 안에 맞추도록 고쳤다(안쪽 프레임과 예외 줄은 남김).
- Slack 이벤트 본문은 `< > &`를 엔티티로 바꿔 보낸다. 파서가 되돌리도록 고쳤다.
- Slack 앱에 Socket Mode가 켜져 있으면 Request URL이 Verified여도 이벤트를 HTTP로 보내지 않는다. 껐더니 이벤트가 들어왔다.
- Incoming Webhook이 올린 봇 메시지(`subtype: bot_message`)도 `message.channels` 이벤트로 온다. 봇 토큰으로 알림을 올리도록 바꿀 필요는 없었다.

가짜 LLM으로 먼저 같은 경로를 확인했다(알림 3,466자 한 메시지, 1초 뒤 스레드에 에스컬레이션 회신, 비용 없음).
