#!/usr/bin/env bash
# 이 실행이 바꾼 것만 되돌리고 잠금을 푼다. 스택(aiops, shopapi)은 커밋된 상태로 계속 둔다. 증거는 지우지 않는다.
set -euo pipefail
. "$(dirname "$0")/_env.sh"
need_run_id
EV="$(evidence_dir)"
holder=$(lock_holder)
if [ -n "$holder" ] && [ "$holder" != "$RUN_ID" ]; then
  echo "잠금이 다른 실행($holder)의 것이다. 그 실행의 스택은 건드리지 않는다" >&2; exit 1
fi
pgrep -f 'scripts.run_cluster' >/dev/null && { echo "run_cluster가 아직 실행 중이다. 끝난 뒤 다시 실행한다(pgrep -fl scripts.run_cluster)" >&2; exit 1; }

# drive가 중간에 죽었으면 버그 이미지나 장애 설정이 남는다. run_cluster의 restore()로 커밋된 매니페스트에 맞춘다
tag=$(committed_tag); dirty=""
for r in $ROLLOUTS; do
  case "${r#*/}" in deploy/*)
    img=$(kubectl -n "${r%%/*}" get "${r#*/}" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)
    [ "${img##*:}" = "$tag" ] || dirty="$dirty ${r%%/*}/${r#*/}";;
  esac
done
if [ -n "$dirty" ] || ! git -C "$ROOT" diff --quiet HEAD -- "$OVERLAY"; then
  echo "커밋된 상태로 되돌린다:${dirty:- overlay 파일}" >&2
  (cd "$ROOT" && .venv/bin/python -c "from scripts.run_cluster import restore; restore()")
fi
git -C "$ROOT" diff --quiet HEAD -- targets/shopapi app || echo "주의: targets/shopapi 또는 app에 커밋 안 된 변경이 남았다. 이 실행이 넣은 버그인지 git diff로 확인한다" >&2

[ -n "$holder" ] && kubectl -n aiops delete configmap "$LOCK" >/dev/null && echo "잠금 해제($RUN_ID)" >&2
[ -d "$EV" ] && echo "증거 보존: $EV" || echo "증거 폴더 없음: $EV" >&2
