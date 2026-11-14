---
service: report
category: code_bug
doc_type: runbook
updated_at: 2026-10-02
---
# 일별 리포트 배치 평균 계산 오류

## 증상

방문이 없는 날 평균 체류 시간을 계산하면서 0으로 나누기 오류가 나고 배치가 실패한다.

## 조치

방문 수가 0이면 평균을 0으로 기록한다.
