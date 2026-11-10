# api/main.py — Les endpoints HTTP
# Lancer :  uvicorn api.main:app --reload     puis ouvrir http://localhost:8000/docs
import logging
from functools import lru_cache

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status

from api.schemas import Answer, AuditCreate, AuditStatus
from api.security import require_api_key
from api.service import AuditBusy, AuditNotFound, AuditService, InvalidAnswer
from api.settings import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")

app = FastAPI(
    title="SEO Agent API",
    version="0.1.0",
    description="Audit SEO d'un site WordPress avec corrections IA et validation humaine (LangGraph).",
)


@lru_cache
def get_service() -> AuditService:
    """Une seule instance pour toute l'API. Import tardif : les tests la remplacent par un faux graphe."""
    from graphe_seo import graphe
    return AuditService(graphe)


@app.get("/health", tags=["système"])
def health() -> dict:
    return {"status": "ok"}


@app.post("/audits", status_code=status.HTTP_202_ACCEPTED, response_model=AuditStatus,
          dependencies=[Depends(require_api_key)], tags=["audits"])
def creer_audit(body: AuditCreate, background: BackgroundTasks,
                service: AuditService = Depends(get_service),
                settings: Settings = Depends(get_settings)):
    """Lance un audit en arrière-plan. Suis-le ensuite avec GET /audits/{id}."""
    sitemap = str(body.sitemap_url or settings.sitemap_url)
    max_pages = body.max_pages or settings.max_pages
    audit_id = service.start(sitemap, max_pages)
    background.add_task(service.run_start, audit_id, sitemap, max_pages)
    return {"audit_id": audit_id, "status": "running"}


@app.get("/audits/{audit_id}", response_model=AuditStatus,
         dependencies=[Depends(require_api_key)], tags=["audits"])
def lire_audit(audit_id: str, service: AuditService = Depends(get_service)):
    """État de l'audit : running, waiting (avec la question), done ou error."""
    try:
        return service.status(audit_id)
    except AuditNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit introuvable")


@app.post("/audits/{audit_id}/resume", status_code=status.HTTP_202_ACCEPTED, response_model=AuditStatus,
          dependencies=[Depends(require_api_key)], tags=["audits"])
def repondre(audit_id: str, body: Answer, background: BackgroundTasks,
             service: AuditService = Depends(get_service)):
    """Envoie ta réponse : un numéro de page ou 'q', puis 'a' / 'r' / 'x'."""
    try:
        service.resume(audit_id, body.answer)
    except AuditNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit introuvable")
    except AuditBusy as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except InvalidAnswer as e:
        raise HTTPException(422, str(e))
    background.add_task(service.run_resume, audit_id, body.answer)
    return {"audit_id": audit_id, "status": "running"}