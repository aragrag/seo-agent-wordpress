# api/schemas.py — Ce que chaque endpoint reçoit et renvoie
from typing import Any, Literal
from pydantic import BaseModel, Field, HttpUrl


class AuditCreate(BaseModel):
    """Corps de POST /audits. Tout est optionnel : sinon on prend les valeurs de .env."""
    sitemap_url: HttpUrl | None = None
    max_pages: int | None = Field(default=None, ge=1, le=500)


class Answer(BaseModel):
    """Corps de POST /audits/{id}/resume : la même réponse que dans le terminal."""
    answer: str = Field(min_length=1, max_length=10, examples=["3", "a", "r", "x", "q"])


class AuditStatus(BaseModel):
    audit_id: str
    status: Literal["running", "waiting", "done", "error"]
    question: dict[str, Any] | None = None   # si "waiting" : la liste des pages ou la proposition
    error: str | None = None                 # si "error"
    result: dict[str, Any] | None = None     # si "done"