#!/usr/bin/env bash
# 사용: RUN_ID=... drive.sh <시나리오...> [run_cluster 옵션]
#   예: drive.sh S14            drive.sh S01 --tenants            drive.sh S02 --kill-worker-after root_cause
# 실제 LLM과 실제 Slack을 쓴다. 누적 비용 상한은 VERIFY_MAX_USD(기본 0.5달러).
set -euo pipefail
. "$(dirname "$0")/_env.sh"
need_run_id
[ "$(lock_holder)" = "$RUN_ID" ] || { echo "잠금이 이 실행($RUN_ID)의 것이 아니다(지금: $(lock_holder)). launch.sh부터 한다" >&2; exit 1; }
[ $# -gt 0 ] || { echo "시나리오 ID를 준다(예: S14)" >&2; exit 2; }
EV="$(evidence_dir)"; mkdir -p "$EV"
"$(dirname "$0")/doctor.sh" > "$EV/doctor_drive_$(date +%H%M%S).txt" || { echo "doctor 실패. ${EV}의 doctor 파일을 본다" >&2; exit 1; }

since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "$since $*" >> "$EV/drives.txt"
set +e
(cd "$ROOT" && caffeinate -i .venv/bin/python -m scripts.run_cluster "$@" --max-usd "${VERIFY_MAX_USD:-0.5}" \
  --tag "verify $RUN_ID" --out "$EV/runs.csv" 2>>"$EV/run_cluster.log" | tee -a "$EV/runs.jsonl")
rc=${PIPESTATUS[0]}
set -e

# 부수 효과: 만들어진 장애, LLM 호출, 승인 조치(DB)와 Slack 메시지, 워커 로그
psql_json "SELECT id, tenant, alertname, category, status, attempts, report_ts, occurred_at, created_at, finished_at, alert_resolved_at FROM incidents WHERE created_at >= '$since' ORDER BY created_at" > "$EV/incidents_$(date +%H%M%S).json"
psql_json "SELECT incident_id, task, model, round(usd::numeric, 6) AS usd, cache_hit FROM llm_calls WHERE created_at >= '$since' ORDER BY created_at" > "$EV/llm_calls_$(date +%H%M%S).json"
psql_json "SELECT incident_id, playbook, target, approver, status, approved_at, finished_at FROM actions WHERE approved_at >= '$since' OR finished_at >= '$since'" > "$EV/actions_$(date +%H%M%S).json"
kubectl -n aiops logs -l app.kubernetes.io/name=ai-worker -c worker --since-time="$since" --prefix --tail=-1 > "$EV/worker_$(date +%H%M%S).log" 2>&1 || true
python3 "$(dirname "$0")/slack_evidence.py" "$EV" "$since" > "$EV/slack_$(date +%H%M%S).json" || echo "Slack 메시지 확인 실패(slack_evidence.py)" >&2

echo "run_cluster 종료 코드 $rc. 증거: $EV" >&2
exit $rc
