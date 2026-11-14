#!/usr/bin/env bash
# aiops-secrets를 클러스터에 만들거나 갱신한다. 값은 Git에 두지 않고 여기서만 읽는다.
#   ANTHROPIC_API_KEY, SLACK_BOT_TOKEN  이 레포의 .env
#   AUTOMATION_TOKEN                     infra_local_set의 .ai-automation-token(Alertmanager가 Bearer로 보내는 값)
#   POSTGRES_PASSWORD, DATABASE_URL      처음에는 무작위로 만들고, 이후에는 클러스터에 있는 값을 유지한다
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRA_DIR="${INFRA_DIR:-$ROOT/..}"
NS=aiops

env_value() { grep -E "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2-; }
pg_password="$(kubectl -n $NS get secret aiops-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null | base64 -d || true)"
[ -n "$pg_password" ] || pg_password="$(openssl rand -hex 16)"

tmp="$(mktemp)"; chmod 600 "$tmp"; trap 'rm -f "$tmp"' EXIT
{
  echo "ANTHROPIC_API_KEY=$(env_value ANTHROPIC_API_KEY)"
  echo "SLACK_BOT_TOKEN=$(env_value SLACK_BOT_TOKEN)"
  echo "AUTOMATION_TOKEN=$(tr -d '\n' < "$INFRA_DIR/.ai-automation-token")"
  echo "POSTGRES_PASSWORD=$pg_password"
  echo "DATABASE_URL=postgresql://aiops:$pg_password@postgres:5432/aiops"
} > "$tmp"
kubectl get ns $NS >/dev/null 2>&1 || kubectl create ns $NS >/dev/null
kubectl -n $NS create secret generic aiops-secrets --from-env-file="$tmp" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
echo "aiops-secrets: $(kubectl -n $NS get secret aiops-secrets -o jsonpath='{.data}' | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin)))')"
