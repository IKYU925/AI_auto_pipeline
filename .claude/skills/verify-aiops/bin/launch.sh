#!/usr/bin/env bash
# 검증 스택을 띄우고 지식 문서까지 넣어 쓸 수 있는 상태로 만든다. 마지막 줄에 RUN_ID를 출력한다.
#   .claude/skills/verify-aiops/bin/launch.sh            가짜 LLM(비용 없음)
#   VERIFY_REAL_LLM=1 .claude/skills/verify-aiops/bin/launch.sh   실제 LLM(Haiku 5.5, Opus 5.5)
set -euo pipefail
source "$(dirname "$0")/_env.sh"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
STATE="$STATE_BASE/$RUN_ID"

if compose ps -q 2>/dev/null | grep -q .; then
  echo "이미 $PROJECT 스택이 떠 있다. 다른 검증이 진행 중일 수 있으니 cleanup.sh를 먼저 실행한다" >&2
  exit 1
fi

mkdir -p "$STATE" "$EVIDENCE_BASE/$RUN_ID"
# 사용 중인 targets/shopapi에는 실험 중 주입된 버그가 있을 수 있어, 커밋된 버전을 꺼내 쓴다
git -C "$ROOT" archive HEAD targets/shopapi | tar -x -C "$STATE"
export VERIFY_SHOPAPI="$STATE/targets/shopapi"
if [ "${VERIFY_REAL_LLM:-0}" = 1 ]; then
  export VERIFY_LIGHT_MODEL=anthropic:claude-haiku-5-5 VERIFY_HEAVY_MODEL=anthropic:claude-opus-5-5
fi
printf 'RUN_ID=%s\nVERIFY_SHOPAPI=%s\nVERIFY_REAL_LLM=%s\n' "$RUN_ID" "$VERIFY_SHOPAPI" "${VERIFY_REAL_LLM:-0}" > "$STATE/run.env"

# api와 worker는 migrate(빈 볼륨에 스키마 생성)가 성공한 뒤에 뜬다
compose up -d --build redis postgres api worker shopapi >/dev/null
for _ in $(seq 1 60); do
  curl -sf localhost:18000/healthz >/dev/null && curl -sf localhost:18100/healthz >/dev/null && break
  sleep 2
done
curl -sf localhost:18000/healthz >/dev/null || { echo "api가 18000에서 응답하지 않는다" >&2; exit 1; }
compose exec -T api python -m scripts.ingest_knowledge knowledge > "$EVIDENCE_BASE/$RUN_ID/ingest.json"
echo "ready"
echo "RUN_ID=$RUN_ID"
