# tests/test_api.py — Teste l'API avec un FAUX graphe : ni GPT, ni WordPress, ni internet.
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from api.main import app, get_service
from api.service import AuditService
from api.settings import Settings, get_settings

CLE = "cle-de-test"
HEADERS = {"X-API-Key": CLE}


# --- Un mini-graphe avec les mêmes pauses que graphe_seo.py ---
class S(TypedDict, total=False):
    sitemap_url: str
    max_pages: int
    choix: str
    decision: str
    sauvegardes: list[str]


def choix_page(state: S) -> dict:
    rep = interrupt({"type": "choix_page", "pages": [{"num": 1}, {"num": 2}]})
    return {"choix": rep}

def validation(state: S) -> dict:
    return {"decision": interrupt({"type": "validation", "correction": {"titre": "Nouveau titre"}})}

def application(state: S) -> dict:
    return {"sauvegardes": state.get("sauvegardes", []) + [f"page-{state['choix']}.json"]}

def faux_graphe():
    b = StateGraph(S)
    b.add_node("choix_page", choix_page)
    b.add_node("validation", validation)
    b.add_node("application", application)
    b.add_edge(START, "choix_page")
    b.add_conditional_edges("choix_page", lambda s: END if s["choix"] == "q" else "validation")
    b.add_conditional_edges("validation", lambda s: "application" if s["decision"] == "a" else "choix_page")
    b.add_edge("application", "choix_page")
    return b.compile(checkpointer=InMemorySaver())


@pytest.fixture
def client():
    """Avant chaque test : une API neuve, avec le faux graphe et une clé connue."""
    service = AuditService(faux_graphe())
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: Settings(api_key=CLE)
    yield TestClient(app)
    app.dependency_overrides.clear()


def creer(client) -> str:
    r = client.post("/audits", json={}, headers=HEADERS)
    assert r.status_code == 202
    return r.json()["audit_id"]


# --- Les tests ---
def test_health_sans_cle(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_cle_obligatoire(client):
    assert client.post("/audits", json={}).status_code == 401
    assert client.post("/audits", json={}, headers={"X-API-Key": "mauvaise"}).status_code == 401


def test_parcours_complet(client):
    audit_id = creer(client)

    etat = client.get(f"/audits/{audit_id}", headers=HEADERS).json()
    assert etat["status"] == "waiting" and etat["question"]["type"] == "choix_page"

    assert client.post(f"/audits/{audit_id}/resume", json={"answer": "2"}, headers=HEADERS).status_code == 202
    etat = client.get(f"/audits/{audit_id}", headers=HEADERS).json()
    assert etat["question"]["type"] == "validation"

    client.post(f"/audits/{audit_id}/resume", json={"answer": "a"}, headers=HEADERS)
    client.post(f"/audits/{audit_id}/resume", json={"answer": "q"}, headers=HEADERS)
    etat = client.get(f"/audits/{audit_id}", headers=HEADERS).json()
    assert etat["status"] == "done"
    assert etat["result"]["sauvegardes"] == ["page-2.json"]


def test_reponse_invalide(client):
    audit_id = creer(client)
    assert client.post(f"/audits/{audit_id}/resume", json={"answer": "99"}, headers=HEADERS).status_code == 422
    client.post(f"/audits/{audit_id}/resume", json={"answer": "1"}, headers=HEADERS)
    assert client.post(f"/audits/{audit_id}/resume", json={"answer": "z"}, headers=HEADERS).status_code == 422


def test_audit_inconnu(client):
    assert client.get("/audits/inexistant", headers=HEADERS).status_code == 404


def test_pas_de_double_reponse(client):
    """Idempotence : si l'audit tourne déjà, une 2e réponse est refusée (pas de double écriture)."""
    audit_id = creer(client)
    service = app.dependency_overrides[get_service]()
    service._en_cours.add(audit_id)       # simule un traitement en cours
    assert client.post(f"/audits/{audit_id}/resume", json={"answer": "1"}, headers=HEADERS).status_code == 409


def test_audit_termine_refuse_reponse(client):
    audit_id = creer(client)
    client.post(f"/audits/{audit_id}/resume", json={"answer": "q"}, headers=HEADERS)
    assert client.post(f"/audits/{audit_id}/resume", json={"answer": "1"}, headers=HEADERS).status_code == 409