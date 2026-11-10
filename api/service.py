# api/service.py — Fait tourner le graphe en arrière-plan et suit l'état de chaque audit.
import logging
import threading
import uuid
from typing import Any

from langgraph.types import Command

log = logging.getLogger("seo_api")


class AuditNotFound(Exception): ...
class AuditBusy(Exception): ...        # l'audit tourne déjà, ou n'attend pas de réponse
class InvalidAnswer(Exception): ...


class AuditService:
    def __init__(self, graphe):
        self.graphe = graphe             # injecté : le vrai graphe, ou un faux dans les tests
        self._lock = threading.Lock()
        self._en_cours: set[str] = set()       # audits en train de tourner
        self._erreurs: dict[str, str] = {}     # dernière erreur par audit

    @staticmethod
    def _config(audit_id: str) -> dict:
        return {"configurable": {"thread_id": audit_id}}

    # --- Lire l'état ---
    def status(self, audit_id: str) -> dict[str, Any]:
        with self._lock:
            if audit_id in self._en_cours:
                return {"audit_id": audit_id, "status": "running"}
            if audit_id in self._erreurs:
                return {"audit_id": audit_id, "status": "error", "error": self._erreurs[audit_id]}

        etat = self.graphe.get_state(self._config(audit_id))   # lu dans SQLite
        if not etat.values and not etat.next:
            raise AuditNotFound(audit_id)

        interrupts = [i for t in etat.tasks for i in t.interrupts]
        if interrupts:
            return {"audit_id": audit_id, "status": "waiting", "question": interrupts[0].value}
        return {"audit_id": audit_id, "status": "done",
                "result": {"sauvegardes": etat.values.get("sauvegardes", [])}}

    # --- Démarrer un audit ---
    def start(self, sitemap_url: str, max_pages: int) -> str:
        audit_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._en_cours.add(audit_id)
        log.info("Audit %s créé (%s, max %d pages)", audit_id, sitemap_url, max_pages)
        return audit_id

    def run_start(self, audit_id: str, sitemap_url: str, max_pages: int) -> None:
        """Appelé en arrière-plan : peut durer plusieurs minutes."""
        self._executer(audit_id, {"sitemap_url": sitemap_url, "max_pages": max_pages})

    # --- Répondre à une pause ---
    def resume(self, audit_id: str, answer: str) -> None:
        """Vérifie la réponse, puis réserve l'audit (évite les doubles clics)."""
        actuel = self.status(audit_id)
        if actuel["status"] != "waiting":
            raise AuditBusy(f"l'audit est '{actuel['status']}', il n'attend pas de réponse")
        self._valider(actuel["question"], answer.strip().lower())
        with self._lock:
            if audit_id in self._en_cours:
                raise AuditBusy("une réponse est déjà en cours de traitement")
            self._en_cours.add(audit_id)
        log.info("Audit %s : réponse '%s'", audit_id, answer)

    def run_resume(self, audit_id: str, answer: str) -> None:
        self._executer(audit_id, Command(resume=answer.strip().lower()))

    # --- Interne ---
    @staticmethod
    def _valider(question: dict, answer: str) -> None:
        if question.get("type") == "choix_page":
            nb = len(question.get("pages", []))
            if answer == "q" or (answer.isdigit() and 1 <= int(answer) <= nb):
                return
            raise InvalidAnswer(f"réponse attendue : un numéro entre 1 et {nb}, ou 'q'")
        if question.get("type") == "validation":
            if answer in ("a", "r", "x"):
                return
            raise InvalidAnswer("réponse attendue : 'a' (approuver), 'r' (refaire) ou 'x' (annuler)")

    def _executer(self, audit_id: str, entree) -> None:
        try:
            self.graphe.invoke(entree, self._config(audit_id))
            with self._lock:
                self._erreurs.pop(audit_id, None)
        except Exception as e:
            log.exception("Audit %s : erreur", audit_id)
            with self._lock:
                self._erreurs[audit_id] = str(e)
        finally:
            with self._lock:
                self._en_cours.discard(audit_id)