#!/usr/bin/env bash
# EKS 클러스터를 만들고(infra_template make eks-up) ArgoCD가 앱을 모두 올릴 때까지 기다린 뒤, 그라파나, 지식 문서, port-forward를 준비한다.
# 마지막 줄이 RUN_ID=... 과금이 시작된다. 끝나면 반드시 cleanup.sh.
set -euo pipefail
. "$(dirname "$0")/_env.sh"
MINUTES="${VERIFY_MINUTES:-60}"

for t in eks-up eks-down; do
  grep -q "^$t:" "$INFRA_DIR/Makefile" 2>/dev/null || { echo "$INFRA_DIR/Makefile에 $t 타깃이 없다. infra_template 경로(INFRA_DIR)나 이름이 바뀌었다(SKILL.md의 infra_template 의존)" >&2; exit 1; }
done
grep -q 'keep-up' "$EKS_SH" 2>/dev/null || { echo "$EKS_SH에 keep-up이 없다(SKILL.md의 infra_template 의존)" >&2; exit 1; }
aws sts get-caller-identity >/dev/null || { echo "AWS 자격 증명이 없다(AWS_PROFILE=$AWS_PROFILE). aws login 뒤 다시" >&2; exit 1; }
for k in ANTHROPIC_API_KEY SLACK_BOT_TOKEN SLACK_APP_TOKEN; do
  grep -qE "^$k=.+" "$ROOT/.env" 2>/dev/null || { echo ".env에 $k 값이 없다" >&2; exit 1; }
done
git -C "$ROOT" diff --quiet HEAD -- targets/shopapi app || { echo "targets/shopapi 또는 app에 커밋 안 된 변경이 있다" >&2; exit 1; }
kept_for "$MINUTES" || { echo "${MINUTES}분 안에 정리 워크플로가 클러스터를 지운다(평일 09:30~12:00, EKS_HOLD_UNTIL로 당일 13:00까지). 시간을 옮기거나 VERIFY_MINUTES를 줄인다" >&2; exit 1; }
[ "$(cluster_status)" = NONE ] || [ "${VERIFY_REUSE_CLUSTER:-}" = 1 ] || {
  echo "클러스터 $CLUSTER_NAME이 이미 있다($(cluster_status)). 다른 세션의 것일 수 있다. 내 것이면 VERIFY_REUSE_CLUSTER=1" >&2; exit 1; }
tag=$(committed_tag)
aws ecr describe-images --repository-name aiops --image-ids imageTag="$tag" >/dev/null 2>&1 || {
  echo "ECR에 $tag가 없다. scripts/build_push.sh로 올리고 deploy/argocd/values.yaml의 imageTag를 main에 병합한다" >&2; exit 1; }

RUN_ID="$(date +%Y%m%d-%H%M%S)"; export RUN_ID
EV="$(evidence_dir)"; ST="$(state_dir)"; mkdir -p "$ST"
export KUBECONFIG="$ST/kubeconfig"
trap 'echo "launch 실패. 클러스터가 남았을 수 있다. RUN_ID=$RUN_ID cleanup.sh로 내린다. 증거: $EV" >&2' ERR

if [ "$(cluster_status)" = NONE ]; then
  touch "$ST/created"
  echo "make eks-up 시작(약 12~14분)" >&2
  make -C "$INFRA_DIR" eks-up > "$EV/eks_up.log" 2>&1
fi
aws eks update-kubeconfig --name "$CLUSTER_NAME" --kubeconfig "$KUBECONFIG" >/dev/null
main_sha > "$ST/main_sha"

echo "ArgoCD 앱 10개 Synced/Healthy 대기(약 4~6분)" >&2
start=$(date +%s)
until [ "$(kubectl -n argocd get applications -o jsonpath='{range .items[*]}{.status.sync.status}/{.status.health.status}{"\n"}{end}' 2>/dev/null | grep -c '^Synced/Healthy$')" -ge 10 ]; do
  [ $(( $(date +%s) - start )) -lt 900 ] || { kubectl -n argocd get applications >&2; echo "15분 안에 앱이 모두 Healthy가 되지 않았다" >&2; false; }
  sleep 10
done
echo "argocd_healthy_sec=$(( $(date +%s) - start ))" >> "$EV/timings.txt"

"$ROOT/scripts/k8s_grafana.sh" >&2
kubectl -n aiops exec deploy/api -c api -- python -m scripts.ingest_knowledge knowledge > "$EV/ingest.json"

for spec in $PF_PORTS; do
  target=${spec%%:*}; ports=${spec#*:}
  ( while true; do kubectl -n "${target%%/*}" port-forward "svc/${target#*/}" "${ports%%:*}:${ports#*:}" >/dev/null 2>&1; sleep 1; done ) &
  echo $! >> "$ST/pf.pids"
done
for p in 19093 19090 18001 18002 18003; do
  for _ in $(seq 30); do curl -s -o /dev/null "localhost:$p" && break; sleep 1; done
done

"$(dirname "$0")/doctor.sh" | tee "$EV/doctor_launch.txt" >&2
echo ready
echo "RUN_ID=$RUN_ID"
