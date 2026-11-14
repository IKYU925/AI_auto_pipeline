#!/usr/bin/env bash
# Grafana가 자동화 이력(PostgreSQL)을 읽도록 읽기 전용 DB 사용자 grafana_ro와 데이터 소스(uid aiops-postgres)를 만든다.
# 다시 실행해도 같은 상태가 된다. 비밀번호는 aiops namespace의 grafana-db Secret에만 있다.
set -euo pipefail
NS=aiops

pw="$(kubectl -n $NS get secret grafana-db -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || true)"
if [ -z "$pw" ]; then
  pw="$(openssl rand -hex 16)"
  kubectl -n $NS create secret generic grafana-db --from-literal=password="$pw" >/dev/null
fi

kubectl -n $NS exec -i postgres-0 -- psql -q -U aiops -d aiops -v ON_ERROR_STOP=1 -v pw="$pw" <<'SQL'
SELECT 'CREATE ROLE grafana_ro LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') \gexec
ALTER ROLE grafana_ro PASSWORD :'pw';
ALTER ROLE grafana_ro SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON incidents, llm_calls, fixes TO grafana_ro;
SQL

# Grafana 데이터 소스 사이드카는 monitoring namespace의 grafana_datasource 라벨 Secret만 읽는다
kubectl -n monitoring create secret generic aiops-postgres-datasource --dry-run=client -o yaml --from-file=aiops-postgres.yaml=/dev/stdin <<EOF | kubectl label --local -f - grafana_datasource=1 -o yaml | kubectl apply -f - >/dev/null
apiVersion: 1
datasources:
  - name: aiops-postgres
    uid: aiops-postgres
    type: grafana-postgresql-datasource
    url: postgres.$NS.svc:5432
    user: grafana_ro
    jsonData: {database: aiops, sslmode: disable, postgresVersion: 1700}
    secureJsonData: {password: "$pw"}
EOF
echo "grafana_ro와 데이터 소스 aiops-postgres 준비됨"
