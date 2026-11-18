#!/usr/bin/env bash
# 사용: RUN_ID=... drive.sh <시나리오...> [run_cluster 옵션]
#   예: drive.sh S14       drive.sh S21       drive.sh S01 --tenants       drive.sh S20(사람 승인 필요)
# 실제 LLM과 실제 Slack을 쓴다. 누적 비용 상한은 VERIFY_MAX_USD(기본 0.5달러).
set -euo pipefail
. "$(dirname "$0")/_env.sh"
need_run_id
[ $# -gt 0 ] || { echo "시나리오 ID를 준다(예: S14)" >&2; exit 2; }
EV="$(evidence_dir)"
"$(dirname "$0")/doctor.sh" > "$EV/doctor_drive_$(date +%H%M%S).txt" || { echo "doctor 실패. ${EV}의 doctor 파일을 본다" >&2; exit 1; }

since=$(date -u +%Y-%m-%dT%H:%M:%SZ); stamp=$(date +%H%M%S)
echo "$since $*" >> "$EV/drives.txt"
set +e
(cd "$ROOT" && export_run_cluster_env && caffeinate -i .venv/bin/python -m scripts.run_cluster "$@" --max-usd "${VERIFY_MAX_USD:-0.5}" \
  --tag "verify-eks $RUN_ID" --out "$EV/runs.csv" 2>>"$EV/run_cluster.log" | tee -a "$EV/runs.jsonl")
rc=${PIPESTATUS[0]}
set -e

psql_json "SELECT id, tenant, alertname, category, status, attempts, report_ts, occurred_at, created_at, finished_at, alert_resolved_at, result->'proposed_action' AS proposed_action, left(report, 1500) AS report FROM incidents WHERE created_at >= '$since' ORDER BY created_at" > "$EV/incidents_$stamp.json"
psql_json "SELECT incident_id, task, model, round(usd::numeric, 6) AS usd, cache_hit FROM llm_calls WHERE created_at >= '$since' ORDER BY created_at" > "$EV/llm_calls_$stamp.json"
psql_json "SELECT incident_id, playbook, target, approver, status, log, approved_at, finished_at FROM actions WHERE approved_at >= '$since' OR finished_at >= '$since'" > "$EV/actions_$stamp.json"
for app in $SHOPAPI_APPS; do
  kubectl -n argocd get application "$app" -o jsonpath='{range .status.history[*]}{.id} {.deployedAt} {.source.kustomize.images[0]}{"\n"}{end}' > "$EV/argo_${app}_$stamp.txt"
done
kubectl -n aiops logs -l app.kubernetes.io/name=ai-worker -c worker --since-time="$since" --prefix --tail=-1 > "$EV/worker_$stamp.log" 2>&1 || true
python3 "$SKILL_DIR/../verify-aiops-cluster/bin/slack_evidence.py" "$EV" "$since" > "$EV/slack_$stamp.json" || echo "Slack 메시지 확인 실패(slack_evidence.py)" >&2

echo "run_cluster 종료 코드 $rc. 증거: $EV" >&2
exit $rc
