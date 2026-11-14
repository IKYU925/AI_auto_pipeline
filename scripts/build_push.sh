#!/usr/bin/env bash
# 이미지를 빌드해 K3s 컨트롤 플레인의 로컬 레지스트리에 올리고, 올린 이미지 이름을 마지막 줄에 출력한다.
# api, worker, shopapi가 같은 이미지를 쓴다.
#   scripts/build_push.sh          태그 base-<git short sha>
#   scripts/build_push.sh s01      버그를 주입한 상태면 시나리오 이름을 붙인다(s01-<sha>)
# 커밋하지 않은 변경(주입한 버그)이 있으면 그 diff의 해시를 태그에 붙인다. 같은 태그를 다시 올리면
# 노드가 이미 받아 둔 이미지를 그대로 써서 버그가 배포되지 않는다(plan.md 9-1).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGISTRY="${REGISTRY:-192.168.56.10:30500}"
export PATH="$PATH:$HOME/.docker/bin"

tag="${1:-base}-$(git -C "$ROOT" rev-parse --short HEAD)"
if ! git -C "$ROOT" diff --quiet HEAD -- targets/shopapi app; then
  tag="$tag-$(git -C "$ROOT" diff HEAD -- targets/shopapi app | shasum | cut -c1-7)"
fi
image="$REGISTRY/aiops:$tag"

docker build --platform linux/arm64 -t "$image" "$ROOT" >&2
# Docker Desktop을 재시작한 직후 첫 push가 no route to host로 실패한 적이 있다(두 번째는 성공)
docker push "$image" >&2 || { sleep 5; docker push "$image" >&2; }
curl -sf "http://$REGISTRY/v2/aiops/tags/list" | grep -q "\"$tag\"" || { echo "레지스트리에서 $tag를 찾지 못했다" >&2; exit 1; }
# overlay가 이 이미지를 쓰게 한다(kustomize CLI 없이)
overlay="$ROOT/deploy/k8s/overlays/local/kustomization.yaml"
[ -f "$overlay" ] && python3 - "$overlay" "$tag" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1]); p.write_text(re.sub(r"newTag: .*", f"newTag: {sys.argv[2]}", p.read_text()))
PY
echo "$image"
