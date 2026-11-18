#!/usr/bin/env bash
# 이 실행이 만든 것만 치운다. port-forward를 끄고, 이 실행이 만든 클러스터면 make eks-down과 남은 리소스 검사까지 한다.
# 증거(results/verify/eks-<RUN_ID>/)는 남긴다. 클러스터를 남기려면 VERIFY_KEEP_CLUSTER=1.
set -euo pipefail
. "$(dirname "$0")/_env.sh"
need_run_id
EV="$(evidence_dir)"; ST="$(state_dir)"
[ -d "$ST" ] || { echo "실행 상태가 없다($ST). 이미 치웠거나 다른 RUN_ID다" >&2; exit 1; }
pgrep -f 'scripts.run_cluster' >/dev/null && { echo "run_cluster가 아직 실행 중이다. 끝난 뒤 다시(pgrep -fl scripts.run_cluster)" >&2; exit 1; }

if [ "$(cluster_status)" = ACTIVE ] && [ -f "$KUBECONFIG" ]; then
  tag=$(committed_tag); dirty=""
  for d in $DEPLOYS; do
    img=$(kubectl -n "${d%%/*}" get deploy "${d#*/}" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
    [ "${img##*:}" = "$tag" ] || dirty="$dirty $d"
  done
  if [ -n "$dirty" ] && [ "${VERIFY_KEEP_CLUSTER:-}" = 1 ]; then
    echo "Git 기준으로 되돌린다:$dirty" >&2
    (cd "$ROOT" && export_run_cluster_env && .venv/bin/python -c "from scripts.run_cluster import restore; restore()")
  fi
fi

if [ -f "$ST/pf.pids" ]; then
  while read -r pid; do pkill -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; done < "$ST/pf.pids"
  echo "port-forward 종료" >&2
fi

if [ -f "$ST/created" ] && [ "${VERIFY_KEEP_CLUSTER:-}" != 1 ]; then
  echo "make eks-down 시작(약 13분)" >&2
  make -C "$INFRA_DIR" eks-down > "$EV/eks_down.log" 2>&1
  grep -a -E 'down took|leftovers' "$EV/eks_down.log" >&2
elif [ ! -f "$ST/created" ]; then
  echo "이 실행이 만든 클러스터가 아니라 내리지 않았다" >&2
fi
rm -rf "$ST"
echo "증거 보존: $EV" >&2
