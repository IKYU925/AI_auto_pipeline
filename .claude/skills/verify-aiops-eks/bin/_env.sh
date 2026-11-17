# launch, doctor, drive, cleanup이 함께 쓰는 값. RUN_ID가 한 번의 검증 실행(클러스터 하나의 수명)을 가리킨다.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
INFRA_DIR="${INFRA_DIR:-$ROOT/../infra_template}"
EKS_SH="$INFRA_DIR/terraform/envs/aiops-eks/eks.sh"
export AWS_PROFILE="${AWS_PROFILE:-ikgyu_admin}" AWS_REGION="${AWS_REGION:-ap-northeast-2}"
CLUSTER_NAME=ai-incident
INFRA_REPO=IKYU925/infra_template
EVIDENCE_BASE="$ROOT/results/verify"
# 클러스터에 인그레스가 없어 port-forward로 붙는다. run_cluster의 환경변수와 같은 포트다
PF_PORTS="monitoring/kube-prometheus-stack-alertmanager:19093:9093 monitoring/kube-prometheus-stack-prometheus:19090:9090 shop/shopapi:18001:8100 tenant-a/shopapi:18002:8100 tenant-b/shopapi:18003:8100"
SHOPAPI_APPS="shopapi-shop shopapi-tenant-a shopapi-tenant-b"
DEPLOYS="shop/shopapi tenant-a/shopapi tenant-b/shopapi aiops/api aiops/worker aiops/approvals"

need_run_id() { [ -n "${RUN_ID:-}" ] || { echo "RUN_ID가 필요하다. launch.sh가 출력한 값을 export RUN_ID=... 로 넣는다" >&2; exit 2; }; }
evidence_dir() { echo "$EVIDENCE_BASE/eks-$RUN_ID"; }
# kubeconfig, port-forward PID 같은 실행 상태. cleanup이 지운다(증거는 남긴다)
state_dir() { echo "$(evidence_dir)/.state"; }
if [ -n "${RUN_ID:-}" ]; then export KUBECONFIG="$(state_dir)/kubeconfig"; fi

# 루트 앱은 origin/main을 배포한다. 그 커밋의 imageTag가 클러스터에 떠야 할 이미지다
committed_tag() { git -C "$ROOT" fetch -q origin main 2>/dev/null; git -C "$ROOT" show origin/main:deploy/argocd/values.yaml | sed -n 's/^imageTag: *//p'; }
main_sha() { git ls-remote "$(git -C "$ROOT" remote get-url origin)" refs/heads/main | cut -f1; }
cluster_status() { aws eks describe-cluster --name "$CLUSTER_NAME" --query cluster.status --output text 2>/dev/null || echo NONE; }
ecr_url() { aws ecr describe-repositories --repository-names aiops --query 'repositories[0].repositoryUri' --output text; }
hold_until() { gh variable get EKS_HOLD_UNTIL -R "$INFRA_REPO" 2>/dev/null || true; }
# 0 = 지금부터 N분 뒤에도 정리 워크플로가 클러스터를 지우지 않는다(코어타임 또는 EKS_HOLD_UNTIL)
kept_for() {
  local t; t=$(TZ=Asia/Seoul date -v+"${1}"M +'%u %H:%M %F')
  # shellcheck disable=SC2086
  bash "$EKS_SH" keep-up $t "$(hold_until)"
}
psql_json() { kubectl -n aiops exec postgres-0 -- psql -U aiops -d aiops -tAc "SELECT coalesce(json_agg(t), '[]') FROM ($1) t"; }
export_run_cluster_env() {
  export CLUSTER=eks ECR_REPOSITORY_URL="$(ecr_url)" ALERTMANAGER_URL=http://127.0.0.1:19093 PROMETHEUS_URL=http://127.0.0.1:19090
  export SHOP_URLS='{"shop":"http://127.0.0.1:18001","tenant-a":"http://127.0.0.1:18002","tenant-b":"http://127.0.0.1:18003"}'
}
