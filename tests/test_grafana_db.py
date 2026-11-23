import base64
import json

import httpx
import psycopg
import pytest
import yaml
from psycopg.conninfo import make_conninfo

from app.grafana_db import KEY, PATH, ensure
from app.kube import Kube


class FakeApi:
    """Secret 하나만 있는 Kubernetes API. server-side apply는 만들거나 덮어쓴다."""

    def __init__(self):
        self.secret = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == PATH
        if request.method == "GET":
            return httpx.Response(200, json=self.secret) if self.secret else httpx.Response(404)
        assert request.headers["content-type"] == "application/apply-patch+yaml"
        self.secret = json.loads(request.content)
        return httpx.Response(200, json=self.secret)

    def password(self) -> str:
        assert self.secret["metadata"]["labels"] == {"grafana_datasource": "1"}
        ds = yaml.safe_load(base64.b64decode(self.secret["data"][KEY]))["datasources"][0]
        assert (ds["uid"], ds["user"]) == ("aiops-postgres", "grafana_ro")
        return ds["secureJsonData"]["password"]


def test_grafana_ro_reads_history_with_the_password_in_the_datasource(database):
    api = FakeApi()
    kube = Kube(httpx.Client(base_url="https://k8s", transport=httpx.MockTransport(api)))

    ensure(kube, database)
    first = api.password()
    ensure(kube, database)
    assert api.password() == first, "다시 돌아도 비밀번호가 바뀌지 않는다"

    with psycopg.connect(make_conninfo(database, user="grafana_ro", password=first)) as conn:
        conn.execute("SELECT count(*) FROM incidents, llm_calls, fixes")
        conn.rollback()
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM incidents")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM approvals")
