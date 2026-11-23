#!/usr/bin/env bash
# 읽기만 한다. FAIL이 하나라도 있으면 종료 코드 1. RUN_ID가 없으면 클러스터 유무와 정리 시각만 본다.
set -uo pipefail
. "$(dirname "$0")/_env.sh"
fail=0
ok() { echo "OK    $*"; }
bad() { echo "FAIL  $*"; fail=1; }
info() { echo "INFO  $*"; }
finish() { [ $fail = 0 ] && echo "doctor: OK" || echo "doctor: FAIL"; exit $fail; }

aws sts get-caller-identity --query Arn --output text >/dev/null 2>&1 && ok "AWS 자격 증명($AWS_PROFILE)" || { bad "AWS 자격 증명 없음(AWS_PROFILE=$AWS_PROFILE)"; finish; }
kept_for 0 && ok "정리 워크플로 보호 시간 안(hold: $(hold_until || true))" || bad "코어타임 밖. 다음 매시 05분 정리 워크플로가 클러스터를 지운다"
kept_for 20 || info "20분 뒤면 보호 시간 밖이다. 남은 시나리오를 줄인다"
status=$(cluster_status)
[ "$status" = ACTIVE ] && ok "클러스터 $CLUSTER_NAME ACTIVE" || { bad "클러스터 $status(launch.sh가 만든다)"; finish; }
[ -n "${RUN_ID:-}" ] && [ -f "$KUBECONFIG" ] || { bad "RUN_ID와 그 실행의 kubeconfig가 필요하다"; finish; }

ready=$(kubectl get nodes --no-headers --request-timeout=15s 2>/dev/null | awk '$2=="Ready"' | wc -l | tr -d ' ')
[ "$ready" -ge 2 ] && ok "노드 ${ready}개 Ready" || bad "Ready 노드 $ready"
apps=$(kubectl -n argocd get applications -o jsonpath='{range .items[*]}{.metadata.name}={.status.sync.status}/{.status.health.status}{"\n"}{end}' 2>/dev/null)
n=$(grep -c '=Synced/Healthy$' <<<"$apps")
[ "$n" -ge 10 ] && ok "ArgoCD 앱 ${n}개 Synced/Healthy" || bad "Synced/Healthy 앱 $n/10: $(grep -v '=Synced/Healthy$' <<<"$apps" | tr '\n' ' ')"
heal=$(kubectl -n argocd get application aiops-root -o jsonpath='{.spec.syncPolicy.automated.selfHeal}' 2>/dev/null)
[ "$heal" = false ] && ok "루트 앱 selfHeal 꺼짐" || bad "루트 앱 selfHeal=$heal. 장애 주입과 롤백이 1초 만에 되돌려진다(infra_template #32)"

tag=$(committed_tag)
for d in $DEPLOYS; do
  img=$(kubectl -n "${d%%/*}" get deploy "${d#*/}" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)
  [ "${img##*:}" = "$tag" ] || bad "$d 이미지 ${img##*:} != $tag(버그 이미지가 남았거나 다른 실험 중)"
done
ok "Deployment 이미지 확인 완료(기준 $tag)"
track=$(kubectl -n shop get deploy shopapi -o jsonpath='{.metadata.annotations.argocd\.argoproj\.io/tracking-id}' 2>/dev/null)
[ "$track" = "shopapi-shop:apps/Deployment:shop/shopapi" ] && ok "ArgoCD 추적 표시 $track" || bad "추적 표시 '$track'(배포 이력을 ArgoCD에서 읽지 못한다)"
sa=system:serviceaccount:aiops
[ "$(kubectl auth can-i list events -n tenant-a --as $sa:ai-api)" = yes ] && ok "ai-api 고객사 이벤트 읽기" || bad "ai-api가 tenant-a 이벤트를 못 읽는다"
[ "$(kubectl auth can-i patch applications.argoproj.io/shopapi-shop -n argocd --as $sa:ai-approvals)" = yes ] && ok "approvals shopapi 앱 patch" || bad "approvals가 shopapi-shop을 patch하지 못한다"
[ "$(kubectl auth can-i patch applications.argoproj.io/aiops -n argocd --as $sa:ai-approvals)" = no ] && ok "approvals aiops 앱 patch 불가" || bad "approvals가 자동화 본체 앱을 바꿀 수 있다"

for k in ANTHROPIC_API_KEY SLACK_BOT_TOKEN SLACK_APP_TOKEN AUTOMATION_TOKEN; do
  v=$(kubectl -n aiops get secret aiops-secrets -o jsonpath="{.data.$k}" 2>/dev/null | base64 -d 2>/dev/null)
  [ -n "$v" ] && ok "Secret $k 있음" || bad "Secret $k 비어 있음(infra_template make eks-secrets)"
done
kubectl -n aiops exec deploy/api -c api -- python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/healthz').read().decode())" 2>/dev/null | grep -q '"ok"' \
  && ok "api /healthz" || bad "api /healthz 응답 없음"
docs=$(kubectl -n aiops exec postgres-0 -- psql -U aiops -d aiops -tAc "SELECT count(*) FROM documents" 2>/dev/null | tr -d ' ')
[ "${docs:-0}" -gt 0 ] && ok "지식 문서 ${docs}개" || bad "지식 문서 0개(launch.sh가 넣는다)"
kubectl -n monitoring get secret alertmanager-kube-prometheus-stack-alertmanager-generated -o jsonpath='{.data.alertmanager\.yaml\.gz}' 2>/dev/null \
  | base64 -d 2>/dev/null | gunzip 2>/dev/null | grep -q 'ai-api.aiops.svc:8000/api/v1/alerts/alertmanager' \
  && ok "Alertmanager 수신처 ai-automation" || bad "Alertmanager 설정에 자동화 웹훅 없음"
t=$(kubectl -n aiops exec postgres-0 -- psql -U aiops -d aiops -tAc "SELECT extract(epoch FROM now())::bigint" 2>/dev/null | tr -d ' ')
skew=$(( ${t:-0} - $(date +%s) )); skew=${skew#-}
[ "$skew" -le 5 ] && ok "DB 시계 차이 ${skew}초" || bad "DB 시계가 ${skew}초 어긋남"

for p in 19093 19090 18001 18002 18003; do
  curl -s -o /dev/null --max-time 5 "localhost:$p" || bad "port-forward $p 응답 없음(launch.sh가 띄운다)"
done
ok "port-forward 확인 완료"
[ "$(main_sha)" = "$(cat "$(state_dir)/main_sha" 2>/dev/null)" ] && ok "launch 뒤 main이 바뀌지 않음" \
  || bad "launch 뒤 main이 바뀌었다. 루트 앱이 다시 sync해 넣은 장애가 풀린다(트러블슈팅 #30). 실험 중에는 병합하지 않는다"
git -C "$ROOT" diff --quiet HEAD -- targets/shopapi app && ok "작업 트리에 주입된 버그 없음" || bad "targets/shopapi 또는 app에 커밋 안 된 변경"
pgrep -f 'scripts.run_cluster' >/dev/null && bad "run_cluster가 이미 실행 중" || ok "다른 run_cluster 실행 없음"
firing=$(curl -sf --max-time 5 "localhost:19093/api/v2/alerts?active=true&silenced=false&inhibited=false" | python3 -c 'import json,sys; print(sum(any(r["name"]=="ai-automation" for r in a["receivers"]) for a in json.load(sys.stdin)))' 2>/dev/null)
info "자동화로 가는 firing 알림 ${firing:-?}개"
finish
