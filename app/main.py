"""FastAPI 진입점. 장애 이벤트(n8n, Alertmanager)를 받아 큐에 넣고 즉시 202를 돌려준다."""
import hmac
import logging
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST

from app.container import Container
from app.alerts import Alert
from app.core.fingerprint import fingerprint
from app.rag.ingest import Document
from app.schemas import IncidentAccepted, IncidentIn, KnowledgeIngestIn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def accept(c: Container, body: IncidentIn) -> IncidentAccepted:
    fp = fingerprint(body.service, body.message, body.stacktrace)
    incident_id = uuid.uuid4().hex[:12]

    # 같은 에러가 알림 폭주로 여러 번 들어와도 LLM 파이프라인은 한 번만 돈다
    existing = c.store.claim_fingerprint(fp, incident_id, c.settings.dedupe_ttl_sec)
    c.metrics.received.labels(source=body.source, deduplicated=str(bool(existing)).lower()).inc()
    if existing:
        current = c.store.get(existing) or {}
        return IncidentAccepted(
            incident_id=existing, status=current.get("status", "queued"), deduplicated=True, fingerprint=fp
        )

    c.store.create(incident_id, fp, body.model_dump(mode="json"))
    c.queue.enqueue({"incident_id": incident_id, "fingerprint": fp, "incident": body.model_dump(mode="json")})
    return IncidentAccepted(incident_id=incident_id, status="queued", fingerprint=fp)


def create_app(container: Container | None = None) -> FastAPI:
    app = FastAPI(title="AI Incident Automation", version="0.1.0")
    app.state.container = container or Container()

    def get_container(request: Request) -> Container:
        return request.app.state.container

    def verify_token(
        x_automation_token: str = Header(default=""), authorization: str = Header(default=""),
        c: Container = Depends(get_container),
    ) -> None:
        # Alertmanager는 Authorization: Bearer로, n8n과 실험 스크립트는 X-Automation-Token으로 보낸다
        token = x_automation_token or authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token, c.settings.automation_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid automation token")

    @app.get("/healthz")
    def healthz(c: Container = Depends(get_container)) -> dict:
        c.redis.ping()
        with c.db.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics(c: Container = Depends(get_container)) -> Response:
        return Response(c.metrics.render(), media_type=CONTENT_TYPE_LATEST)

    @app.post(
        "/api/v1/incidents",
        response_model=IncidentAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(verify_token)],
    )
    def create_incident(body: IncidentIn, c: Container = Depends(get_container)) -> IncidentAccepted:
        return accept(c, body)

    @app.post("/api/v1/alerts/alertmanager", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(verify_token)])
    def alertmanager_webhook(payload: dict, c: Container = Depends(get_container)) -> dict:
        accepted, already_open, resolved = [], [], 0
        for alert in (Alert.parse(raw) for raw in payload.get("alerts", [])):
            if alert.status == "resolved":
                resolved += c.store.resolve_alert(alert.fingerprint, alert.ends_at or datetime.now(timezone.utc))
                continue
            if open_id := c.store.open_alert_incident(alert.fingerprint):
                already_open.append(open_id)
                continue
            stacktrace = None
            if alert.kind.needs_stacktrace and c.loki:
                stacktrace = c.loki.latest_exception(alert.labels.get("namespace", "shop"), alert.target,
                                                     alert.starts_at, datetime.now(timezone.utc))
            accepted.append(accept(c, alert.to_incident(c.settings.alert_slack_channel, stacktrace)).incident_id)
        return {"accepted": accepted, "already_open": already_open, "resolved": resolved}

    @app.get("/api/v1/incidents/{incident_id}", dependencies=[Depends(verify_token)])
    def get_incident(incident_id: str, c: Container = Depends(get_container)) -> dict:
        data = c.store.get(incident_id)
        if not data:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
        return {"incident_id": incident_id, **data}

    @app.get("/api/v1/config", dependencies=[Depends(verify_token)])
    def get_config(c: Container = Depends(get_container)) -> dict:
        """실험 스크립트가 기록할 조건과 서버의 실제 설정이 같은지 확인하는 데 쓴다."""
        s = c.settings
        return {
            "light_model": s.light_model,
            "heavy_model": s.heavy_model,
            "max_fix_attempts": s.max_fix_attempts,
            "incident_budget_usd": s.incident_budget_usd,
            "checkpoint_enabled": s.checkpoint_enabled,
            "optimizations": {name.removeprefix("opt_"): getattr(s, name) for name in type(s).model_fields if name.startswith("opt_")},
        }

    @app.post("/api/v1/knowledge", dependencies=[Depends(verify_token)])
    def ingest_knowledge(body: KnowledgeIngestIn, c: Container = Depends(get_container)) -> dict:
        docs = [Document(**d.model_dump()) for d in body.documents]
        report = c.ingestor.ingest(docs)
        return {**report.to_dict(), "embedding_cache": c.embedder.stats}

    return app


app = create_app()
