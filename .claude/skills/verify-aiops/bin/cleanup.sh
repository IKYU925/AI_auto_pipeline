#!/usr/bin/env bash
# 이 검증이 띄운 aiops-verify 스택과 데이터 볼륨, 임시 shopapi 복사본을 지운다. 증거(results/verify/<RUN_ID>)는 남긴다.
set -euo pipefail
source "$(dirname "$0")/_env.sh"
need_run_id
export VERIFY_SHOPAPI="$STATE_BASE/$RUN_ID/targets/shopapi"
compose down -v --remove-orphans >/dev/null 2>&1 || true
rm -rf "${STATE_BASE:?}/$RUN_ID"
echo "cleaned $PROJECT, evidence kept at $EVIDENCE_BASE/$RUN_ID"
