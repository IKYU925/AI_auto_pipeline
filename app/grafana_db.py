"""Grafana가 자동화 이력을 읽는 읽기 전용 DB 사용자 grafana_ro와 데이터 소스(uid aiops-postgres)를 맞춘다.

api Pod의 init 컨테이너로 뜰 때마다 돈다(멱등). 비밀번호는 Grafana 사이드카가 읽는 monitoring namespace의
Secret 하나에만 있다. 그 Secret이 없으면 새 비밀번호를 만들고, 있으면 그 값을 DB에 다시 맞춘다.
"""
import base64
import secrets
import sys

import psycopg
import yaml
from psycopg import sql

from app.kube import Kube

NAMESPACE = "monitoring"
SECRET = "aiops-postgres-datasource"
KEY = "aiops-postgres.yaml"
PATH = f"/api/v1/namespaces/{NAMESPACE}/secrets/{SECRET}"


def datasource(password: str) -> str:
    return yaml.safe_dump({"apiVersion": 1, "datasources": [{
        "name": "aiops-postgres", "uid": "aiops-postgres", "type": "grafana-postgresql-datasource",
        "url": "postgres.aiops.svc:5432", "user": "grafana_ro",
        "jsonData": {"database": "aiops", "sslmode": "disable", "postgresVersion": 1700},
        "secureJsonData": {"password": password},
    }]})


def stored_password(kube: Kube) -> str | None:
    resp = kube.http.get(PATH)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return yaml.safe_load(base64.b64decode(resp.json()["data"][KEY]))["datasources"][0]["secureJsonData"]["password"]


def ensure(kube: Kube, database_url: str) -> None:
    password = stored_password(kube) or secrets.token_hex(16)
    with psycopg.connect(database_url, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro'").fetchone():
            conn.execute("CREATE ROLE grafana_ro LOGIN")
        conn.execute(sql.SQL("ALTER ROLE grafana_ro PASSWORD {}").format(password))
        conn.execute("ALTER ROLE grafana_ro SET default_transaction_read_only = on")
        conn.execute("GRANT USAGE ON SCHEMA public TO grafana_ro")
        conn.execute("GRANT SELECT ON incidents, llm_calls, fixes TO grafana_ro")
    # 이름이 URL에 있는 server-side apply라 Role의 resourceNames로 이 Secret 하나만 허용할 수 있다
    secret = {"apiVersion": "v1", "kind": "Secret",
              "metadata": {"name": SECRET, "namespace": NAMESPACE, "labels": {"grafana_datasource": "1"}},
              "data": {KEY: base64.b64encode(datasource(password).encode()).decode()}}
    kube.http.patch(PATH, params={"fieldManager": "aiops", "force": "true"}, json=secret,
                    headers={"Content-Type": "application/apply-patch+yaml"}).raise_for_status()


if __name__ == "__main__":
    from app.config import get_settings

    try:
        ensure(Kube(), get_settings().database_url)
        print("grafana_ro와 데이터 소스 aiops-postgres 준비됨")
    except Exception as e:  # 대시보드 데이터 소스 때문에 장애 접수(api)가 뜨지 못하면 안 된다
        print(f"grafana_ro 준비 실패: {e!r}", file=sys.stderr)
