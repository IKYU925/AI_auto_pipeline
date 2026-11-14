#!/usr/bin/env bash
# 읽기만 하는 점검. 이 인스턴스를 검증에 써도 되는지 답한다.
set -uo pipefail
source "$(dirname "$0")/_env.sh"
need_run_id
set -a; source "$ROOT/.env"; source "$STATE_BASE/$RUN_ID/run.env"; set +a
ok=1
check() { if eval "$2" >/dev/null 2>&1; then echo "OK   $1"; else echo "FAIL $1"; ok=0; fi; }
check "컨테이너 6개 실행 중(redis, qdrant, api, worker 2, shopapi)" '[ "$(compose ps --status running -q | wc -l | tr -d " ")" = 6 ]'
check "18000 포트가 aiops-verify api" 'compose port api 8000 | grep -q 18000'
check "api 상태 확인(/healthz)" 'curl -sf localhost:18000/healthz'
check "shopapi 상태 확인(18100)" 'curl -sf localhost:18100/healthz'
check "토큰 인증과 최적화 모두 켬(/api/v1/config)" 'curl -sf localhost:18000/api/v1/config -H "X-Automation-Token: $AUTOMATION_TOKEN" | grep -q "\"fix_reuse\":true"'
want=fake:light; [ "$VERIFY_REAL_LLM" = 1 ] && want=anthropic:claude-haiku-5-5
check "경량 모델이 $want" 'curl -sf localhost:18000/api/v1/config -H "X-Automation-Token: $AUTOMATION_TOKEN" | grep -q "\"light_model\":\"$want\""'
fresh="$(mktemp -d)"; git -C "$ROOT" archive HEAD targets/shopapi | tar -x -C "$fresh"
check "shopapi 복사본이 커밋된 버전과 같음(주입된 버그 없음)" 'diff -rq -x __pycache__ -x .pytest_cache "$fresh/targets/shopapi" "$VERIFY_SHOPAPI"'
rm -rf "$fresh"
[ $ok = 1 ] && echo "doctor: 이 인스턴스로 검증해도 된다" || { echo "doctor: 문제가 있다. cleanup.sh 후 launch.sh로 다시 띄운다"; exit 1; }
