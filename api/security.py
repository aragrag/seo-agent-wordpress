# api/security.py — Protection par clé API
import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from api.settings import Settings, get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(cle: str | None = Depends(api_key_header),
                    settings: Settings = Depends(get_settings)) -> None:
    if not settings.api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "API_KEY non configurée sur le serveur")
    if not cle or not secrets.compare_digest(cle, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé API invalide ou manquante")