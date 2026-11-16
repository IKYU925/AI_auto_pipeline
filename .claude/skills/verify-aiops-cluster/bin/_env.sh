# launch, doctor, drive, cleanup이 함께 쓰는 값. RUN_ID가 한 번의 검증 실행을 가리킨다.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
INFRA_DIR="${INFRA_DIR:-$ROOT/../infra_local_set}"
export KUBECONFIG="${KUBECONFIG_OVERRIDE:-$INFRA_DIR/kubeconfig}"
export PATH="$PATH:$HOME/.docker/bin"
REGISTRY=192.168.56.10:30500
PROMETHEUS=http://192.168.56.10:30090
ALERTMANAGER=http://192.168.56.10:30093
OVERLAY="$ROOT/deploy/k8s/overlays/local/kustomization.yaml"
LOCK=verify-aiops-cluster-lock
EVIDENCE_BASE="$ROOT/results/verify"
ROLLOUTS="shop/deploy/shopapi tenant-a/deploy/shopapi tenant-b/deploy/shopapi aiops/deploy/api aiops/deploy/worker aiops/deploy/approvals aiops/sts/postgres aiops/sts/redis"

# 커밋된 overlay의 이미지 태그. run_cluster의 restore()가 이 태그로 되돌리므로 레지스트리에 있어야 한다
committed_tag() { git -C "$ROOT" show HEAD:deploy/k8s/overlays/local/kustomization.yaml | sed -n 's/.*newTag: *//p'; }
psql_json() { kubectl -n aiops exec postgres-0 -- psql -U aiops -d aiops -tAc "SELECT coalesce(json_agg(t), '[]') FROM ($1) t"; }
lock_holder() { kubectl -n aiops get configmap "$LOCK" -o jsonpath='{.data.run_id}' 2>/dev/null; }
need_run_id() { [ -n "${RUN_ID:-}" ] || { echo "RUN_ID가 필요하다. launch.sh가 출력한 값을 export RUN_ID=... 로 넣는다" >&2; exit 2; }; }
evidence_dir() { echo "$EVIDENCE_BASE/cluster-$RUN_ID"; }
