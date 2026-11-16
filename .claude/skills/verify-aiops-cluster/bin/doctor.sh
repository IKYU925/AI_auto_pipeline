#!/usr/bin/env bash
# 읽기만 한다. FAIL이 하나라도 있으면 종료 코드 1.
set -uo pipefail
. "$(dirname "$0")/_env.sh"
fail=0
ok() { echo "OK    $*"; }
bad() { echo "FAIL  $*"; fail=1; }
info() { echo "INFO  $*"; }

ready=$(kubectl get nodes --no-headers --request-timeout=10s 2>/dev/null | awk '$2=="Ready"' | wc -l | tr -d ' ')
[ "$ready" = 3 ] && ok "노드 3개 Ready" || { bad "Ready 노드 $ready/3 (infra_local_set의 verify-k3s-lab로 클러스터를 먼저 확인)"; echo "doctor: FAIL"; exit 1; }

tag=$(committed_tag)
curl -sf "http://$REGISTRY/v2/aiops/tags/list" | grep -q "\"$tag\"" && ok "레지스트리에 커밋된 overlay 이미지 $tag" || bad "레지스트리에 $tag 없음(launch.sh가 만든다)"

for r in $ROLLOUTS; do
  ns=${r%%/*}; obj=${r#*/}
  want=$(kubectl -n "$ns" get "$obj" -o jsonpath='{.spec.replicas}' 2>/dev/null)
  have=$(kubectl -n "$ns" get "$obj" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)
  [ -n "$want" ] && [ "${have:-0}" = "$want" ] && ok "$ns/$obj $have/$want" || bad "$ns/$obj 준비 ${have:-0}/${want:-없음}"
  case "$obj" in deploy/*)
    img=$(kubectl -n "$ns" get "$obj" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)
    [ "${img##*:}" = "$tag" ] || bad "$ns/$obj 이미지 ${img##*:} != $tag (버그 이미지가 남았거나 다른 실험 중)";;
  esac
done

for k in ANTHROPIC_API_KEY SLACK_BOT_TOKEN SLACK_APP_TOKEN AUTOMATION_TOKEN; do
  v=$(kubectl -n aiops get secret aiops-secrets -o jsonpath="{.data.$k}" 2>/dev/null | base64 -d 2>/dev/null)
  [ -n "$v" ] && ok "Secret $k 있음" || bad "Secret $k 비어 있음(.env를 채우고 launch.sh)"
done

kubectl -n aiops exec deploy/api -c api -- python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/healthz').read().decode())" 2>/dev/null | grep -q '"ok"' \
  && ok "api /healthz" || bad "api /healthz 응답 없음"
docs=$(kubectl -n aiops exec postgres-0 -- psql -U aiops -d aiops -tAc "SELECT count(*) FROM documents" 2>/dev/null | tr -d ' ')
[ "${docs:-0}" -gt 0 ] && ok "지식 문서 ${docs}개" || bad "지식 문서 0개(launch.sh가 넣는다)"

# /api/v2/status는 웹훅 URL을 <secret>으로 가린다. operator가 만든 실제 설정을 읽는다
kubectl -n monitoring get secret alertmanager-kube-prometheus-stack-alertmanager-generated -o jsonpath='{.data.alertmanager\.yaml\.gz}' 2>/dev/null \
  | base64 -d 2>/dev/null | gunzip 2>/dev/null | grep -q 'ai-api.aiops.svc:8000/api/v1/alerts/alertmanager' \
  && ok "Alertmanager 수신처 ai-automation(웹훅 URL)" || bad "Alertmanager 설정에 자동화 웹훅 없음(infra_local_set 쪽 문제)"

now=$(date +%s)
for name in DB Prometheus; do
  if [ $name = DB ]; then t=$(kubectl -n aiops exec postgres-0 -- psql -U aiops -d aiops -tAc "SELECT extract(epoch FROM now())::bigint" 2>/dev/null | tr -d ' ')
  else t=$(curl -sf "$PROMETHEUS/api/v1/query?query=time()" | python3 -c 'import json,sys; print(int(float(json.load(sys.stdin)["data"]["result"][0])))' 2>/dev/null); fi
  skew=$(( ${t:-0} - now )); skew=${skew#-}
  [ "$skew" -le 5 ] && ok "$name 시계 차이 ${skew}초" || bad "$name 시계가 ${skew}초 어긋남(맥이 잠들었었다면 노드 시계를 맞춘다)"
done

git -C "$ROOT" diff --quiet HEAD -- targets/shopapi app && ok "작업 트리 코드가 HEAD와 같음(주입된 버그 없음)" || bad "targets/shopapi 또는 app에 커밋 안 된 변경(다른 실험이 버그를 주입한 상태일 수 있음)"
pgrep -f 'scripts.run_cluster' >/dev/null && bad "이 맥에서 run_cluster가 이미 실행 중(pgrep -fl scripts.run_cluster)" || ok "다른 run_cluster 실행 없음"

firing=$(curl -sf "$ALERTMANAGER/api/v2/alerts?active=true&silenced=false&inhibited=false" | python3 -c 'import json,sys; print(sum(any(r["name"]=="ai-automation" for r in a["receivers"]) for a in json.load(sys.stdin)))' 2>/dev/null)
info "자동화로 가는 firing 알림 ${firing:-?}개(run_cluster가 시작 전 해소를 기다린다)"
holder=$(lock_holder); info "잠금: ${holder:-없음}${RUN_ID:+ (이 실행 $RUN_ID)}"

[ $fail = 0 ] && echo "doctor: OK" || echo "doctor: FAIL"
exit $fail
