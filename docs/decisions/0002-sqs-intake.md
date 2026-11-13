# 0002. AWS 알림은 EventBridge와 SQS로 받는다

상태: 채택
날짜: 2026-11-14

## 맥락

EC2 장애는 CloudWatch 경보로 감지한다. 경보를 자동화로 전달하는 방법으로 처음에는 SNS HTTPS 구독을 계획했다.

## 결정

CloudWatch 경보 상태 변경 이벤트를 EventBridge 규칙으로 SQS에 넣고, 로컬 K3s의 자동화가 SQS에서 가져간다.

## 근거

- SNS HTTPS 구독은 자동화가 공개 주소로 받아야 해서 로드밸런서, 구독 확인, 서명 검증이 필요하다. SQS는 자동화가 가져가는 방식이라 공개 입구가 없다.
- 자동화가 꺼져 있던 동안의 경보가 큐에 남는다. 처리를 마친 메시지만 지우면 중간에 죽어도 다시 처리된다.
- 고객 계정의 EventBridge가 운영 계정의 이벤트 버스로 보내는 구조로 넓히기 쉽다.

## 영향

- 자동화에 SQS 소비자와 CloudWatch Logs 조회가 들어간다(F07).
- 로컬 자동화는 SQS 읽기와 삭제, Logs 조회만 허용한 전용 IAM 사용자 키를 쓰고, 4단계가 끝나면 키를 지운다.

## 검토한 대안

- SNS HTTPS 구독: 공개 입구와 서명 검증이 필요하다.
- EventBridge API destination: 공개 HTTPS 입구가 필요하다.
