#!/usr/bin/env bash
# 시나리오 버그를 shopapi에 주입하거나 원복한다.
#   scripts/inject.sh S01          주입
#   scripts/inject.sh S01 revert   원복
set -euo pipefail
cd "$(dirname "$0")/.."
id="${1:?scenario id required}"
patch="scenarios/${id}.patch"
[ -f "$patch" ] || { echo "no such scenario: $patch" >&2; exit 1; }

if [ "${2:-}" = "revert" ]; then
  git apply -R --directory=targets/shopapi "$patch"
  echo "reverted $id"
else
  git apply --directory=targets/shopapi "$patch"
  echo "injected $id"
fi
