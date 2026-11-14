#!/usr/bin/env bash
# 시나리오를 실제 사용자 경로(버그 주입, 재현, /api/v1/incidents 접수, 워커 처리, 판정, 원복)로 돌리고 증거를 남긴다.
#   RUN_ID=... .claude/skills/verify-aiops/bin/drive.sh S10 S11 S13
set -euo pipefail
source "$(dirname "$0")/_env.sh"
need_run_id
set -a; source "$ROOT/.env"; source "$STATE_BASE/$RUN_ID/run.env"; set +a
OUT="$EVIDENCE_BASE/$RUN_ID"
cost_cap=(); [ "$VERIFY_REAL_LLM" = 1 ] && cost_cap=(--max-usd "${VERIFY_MAX_USD:-0.5}")
cd "$ROOT"
SHOPAPI_DIR="$VERIFY_SHOPAPI" .venv/bin/python -m scripts.run_experiment "$@" --condition A --repeat 1 \
  --api http://localhost:18000 --redis redis://localhost:16379/0 --database postgresql://aiops:aiops@localhost:15432/aiops ${cost_cap[@]+"${cost_cap[@]}"} --timeout 300 \
  --out "$OUT/runs.csv" | tee -a "$OUT/runs.jsonl"
.venv/bin/python - "$OUT" <<'PY'
import csv, json, subprocess, sys
out = sys.argv[1]
for row in csv.DictReader(open(f"{out}/runs.csv")):
    raw = subprocess.run(["curl", "-sf", f"localhost:18000/api/v1/incidents/{row['incident_id']}",
                          "-H", f"X-Automation-Token: {__import__('os').environ['AUTOMATION_TOKEN']}"], capture_output=True, text=True).stdout
    open(f"{out}/incident_{row['scenario']}_{row['incident_id']}.json", "w").write(raw)
PY
compose logs --no-color worker > "$OUT/worker.log" 2>&1
echo "evidence: $OUT"
