#!/usr/bin/env bash
# 로컬 K3s에 자동화 스택을 커밋된 상태로 맞추고 잠금을 잡는다. 마지막 줄이 RUN_ID=...
set -euo pipefail
. "$(dirname "$0")/_env.sh"

kubectl get nodes --request-timeout=10s >/dev/null || { echo "클러스터에 접속할 수 없다. infra_local_set에서 ./up.sh 또는 verify-k3s-lab로 먼저 띄운다" >&2; exit 1; }
for k in ANTHROPIC_API_KEY SLACK_BOT_TOKEN SLACK_APP_TOKEN; do
  grep -qE "^$k=.+" "$ROOT/.env" 2>/dev/null || { echo ".env에 $k 값이 없다" >&2; exit 1; }
done
git -C "$ROOT" diff --quiet HEAD -- targets/shopapi app || { echo "targets/shopapi 또는 app에 커밋 안 된 변경이 있다. 버그가 주입된 채로 배포하지 않는다" >&2; exit 1; }

RUN_ID="$(date +%Y%m%d-%H%M%S)"
kubectl get ns aiops >/dev/null 2>&1 || kubectl create ns aiops >/dev/null
if ! kubectl -n aiops create configmap "$LOCK" --from-literal=run_id="$RUN_ID" --from-literal=host="$(hostname)" >/dev/null 2>&1; then
  echo "다른 검증($(lock_holder))이 클러스터를 쓰는 중이다. 끝난 실행이면 RUN_ID=$(lock_holder) cleanup.sh로 푼다" >&2; exit 1
fi
trap 'kubectl -n aiops delete configmap "$LOCK" >/dev/null 2>&1; echo "launch 실패. 잠금을 풀었다. 증거: $EV" >&2' ERR
EV="$(evidence_dir)"; mkdir -p "$EV"

tag=$(committed_tag)
if ! curl -sf "http://$REGISTRY/v2/aiops/tags/list" | grep -q "\"$tag\""; then
  # 클러스터를 새로 만들면 레지스트리가 비어 있다. 커밋된 태그의 코드가 HEAD와 같을 때만 HEAD로 빌드해 그 태그로 올린다
  sha=${tag##*-}
  git -C "$ROOT" diff --quiet "$sha" HEAD -- app targets/shopapi Dockerfile requirements.txt migrations knowledge scripts || {
    echo "레지스트리에 ${tag}가 없고 코드가 $sha 이후 바뀌었다. scripts/build_push.sh 뒤 overlay 태그 변경을 커밋한다" >&2; exit 1; }
  echo "레지스트리에 ${tag}가 없어 HEAD로 빌드한다(코드가 ${sha}와 같음)" >&2
  docker build --platform linux/arm64 -t "$REGISTRY/aiops:$tag" "$ROOT" >&2
  docker push "$REGISTRY/aiops:$tag" >&2 || { sleep 5; docker push "$REGISTRY/aiops:$tag" >&2; }
fi

"$ROOT/scripts/k8s_secrets.sh" >&2
kubectl apply -k "$(dirname "$OVERLAY")" >/dev/null
for r in $ROLLOUTS; do kubectl -n "${r%%/*}" rollout status "${r#*/}" --timeout=300s >&2; done
"$ROOT/scripts/k8s_grafana.sh" >&2
# 바뀐 청크만 임베딩하므로 다시 돌려도 싸다. api 컨테이너 안에서 돌면 잠시 메모리가 600Mi 넘게 오른다
kubectl -n aiops exec deploy/api -c api -- python -m scripts.ingest_knowledge knowledge > "$EV/ingest.json"

"$(dirname "$0")/doctor.sh" | tee "$EV/doctor_launch.txt" >&2
echo ready
echo "RUN_ID=$RUN_ID"
