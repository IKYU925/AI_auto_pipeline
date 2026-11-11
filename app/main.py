"""FastAPI 진입점. n8n이 가공한 장애 이벤트를 받아 큐에 넣고 즉시 202를 돌려준다."""
import hmac
import logging
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status

from app.container import Container
from app.core.fingerprint import fingerprint
from app.rag.ingest import Document
from app.schemas import IncidentAccepted, IncidentIn, KnowledgeIngestIn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(container: Container | None = None) -> FastAPI:
    app = FastAPI(title="AI Incident Automation", version="0.1.0")
    app.state.container = container or Container()

    def get_container(request: Request) -> Container:
        return request.app.state.container

    def verify_token(
        x_automation_token: str = Header(default=""), c: Container = Depends(get_container)
    ) -> None:
        if not hmac.compare_digest(x_automation_token, c.settings.automation_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid automation token")

    @app.get("/healthz")
    def healthz(c: Container = Depends(get_container)) -> dict:
        c.redis.ping()
        return {"status": "ok"}

    @app.post(
        "/api/v1/incidents",
        response_model=IncidentAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(verify_token)],
    )
    def create_incident(body: IncidentIn, c: Container = Depends(get_container)) -> IncidentAccepted:
        fp = fingerprint(body.service, body.message, body.stacktrace)
        incident_id = uuid.uuid4().hex[:12]

        # 같은 에러가 알림 폭주로 여러 번 들어와도 LLM 파이프라인은 한 번만 돈다
        existing = c.store.claim_fingerprint(fp, incident_id, c.settings.dedupe_ttl_sec)
        if existing:
            current = c.store.get(existing) or {}
            return IncidentAccepted(
                incident_id=existing, status=current.get("status", "queued"), deduplicated=True, fingerprint=fp
            )

        c.store.set_status(incident_id, "queued", fingerprint=fp)
        c.queue.enqueue({"incident_id": incident_id, "fingerprint": fp, "incident": body.model_dump(mode="json")})
        return IncidentAccepted(incident_id=incident_id, status="queued", fingerprint=fp)

    @app.get("/api/v1/incidents/{incident_id}", dependencies=[Depends(verify_token)])
    def get_incident(incident_id: str, c: Container = Depends(get_container)) -> dict:
        data = c.store.get(incident_id)
        if not data:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
        return {"incident_id": incident_id, **data}

    @app.post("/api/v1/knowledge", dependencies=[Depends(verify_token)])
    def ingest_knowledge(body: KnowledgeIngestIn, c: Container = Depends(get_container)) -> dict:
        docs = [Document(**d.model_dump()) for d in body.documents]
        report = c.ingestor.ingest(docs)
        return {**report.to_dict(), "embedding_cache": c.embedder.stats}

    return app


app = create_app()
