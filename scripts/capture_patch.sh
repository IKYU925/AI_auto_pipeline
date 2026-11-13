#!/usr/bin/env bash
# targets/shopapi에 직접 넣은 버그를 시나리오 패치로 저장하고 원본으로 되돌린다.
#   (shopapi 코드를 고친 뒤) scripts/capture_patch.sh S02
set -euo pipefail
cd "$(dirname "$0")/.."
id="${1:?scenario id required}"
out="scenarios/${id}.patch"
git diff --quiet -- targets/shopapi && { echo "targets/shopapi에 바뀐 내용이 없다" >&2; exit 1; }
git diff --relative=targets/shopapi -- targets/shopapi > "$out"
git checkout -- targets/shopapi
echo "saved $out"
