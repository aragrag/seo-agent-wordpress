# graphe_seo.py — Audit SEO avec LangGraph
# Étape 3 : ton menu de audit_interactif.py, traduit en graphe + validation humaine
#
#  START → audit → detection ─(aucune erreur)→ END
#                      │
#                      ▼
#                ┌─→ choix_page ⏸️ ─(q)→ END
#                │       │
#                │       ▼
#                │   corrections ←──────┐
#                │       │              │ (refaire)
#                │       ▼              │
#                │   validation ⏸️ ─────┘
#                │       │   └─(annuler)→ choix_page
#                │       ▼ (approuvé)
#                └── application   (sauvegarde JSON + WordPress, puis ré-analyse de la page
#                                   pour mettre la liste à jour)
import os
import sys
import time
import sqlite3
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command

from sitemap import lire_sitemap
from analyseur import analyser_page
from regles_seo import detecter_problemes
from correcteur import generer_corrections, sauvegarder, Corrections
import wordpress



# --- 1. Le State ---
class AuditState(TypedDict, total=False):
    sitemap_url: str
    max_pages: int
    pages: list[dict]               # "audit"
    pages_en_echec: list[dict]      # "audit"
    pages_avec_erreurs: list[dict]  # "detection" : {"page": ..., "problemes": [...]}
    page_choisie: int | None        # "choix_page" : index dans pages_avec_erreurs
    quitter: bool                   # "choix_page"
    correction: dict | None         # "corrections" : proposition de l'IA pour la page choisie
    decision: str                   # "validation" : "a" (approuver), "r" (refaire), "x" (annuler)
    sauvegardes: list[str]          # "application" : fichiers JSON écrits
    plans_wp: list[dict]            # "application" : ce qui serait envoyé à WordPress


# --- 2. Les nœuds ---
def audit(state: AuditState) -> dict:
    """Lit le sitemap et analyse chaque page."""
    urls = lire_sitemap(state["sitemap_url"])[: state.get("max_pages", 50)]
    print(f"📄 {len(urls)} pages à analyser")
    pages, echecs = [], []
    for i, url in enumerate(urls, 1):
        print(f"  [{i}/{len(urls)}] {url}")
        try:
            data = analyser_page(url)
            data["url"] = url
            pages.append(data)
        except Exception as e:
            echecs.append({"url": url, "erreur": str(e)})
        time.sleep(0.3)
    return {"pages": pages, "pages_en_echec": echecs}


def detection(state: AuditState) -> dict:
    """Applique les règles SEO à chaque page analysée."""
    avec_erreurs = []
    for page in state["pages"]:
        problemes = detecter_problemes(page)
        if problemes:
            avec_erreurs.append({"page": page, "problemes": problemes})
    return {"pages_avec_erreurs": avec_erreurs}


def choix_page(state: AuditState) -> dict:
    """⏸️ Pause : montre la liste et attend le numéro choisi (remplace input())."""
    liste = [{"num": i, "url": item["page"]["url"], "nb_problemes": len(item["problemes"])}
             for i, item in enumerate(state["pages_avec_erreurs"], 1)]
    reponse = str(interrupt({"type": "choix_page", "pages": liste})).strip().lower()

    if reponse in ("q", "quit", "exit"):
        return {"quitter": True}
    if reponse.isdigit() and 1 <= int(reponse) <= len(liste):
        return {"quitter": False, "page_choisie": int(reponse) - 1}
    print("❌ Numéro invalide.")
    return {"quitter": False, "page_choisie": None}


def corrections(state: AuditState) -> dict:
    """Appel IA pour la page choisie uniquement (pas d'interrupt ici : l'IA ne doit tourner qu'une fois)."""
    item = state["pages_avec_erreurs"][state["page_choisie"]]
    print(f"\n   ⏳ L'IA analyse {item['page']['url']} et ses images...")
    try:
        resultat = generer_corrections(item["page"], item["problemes"])
        if not (resultat.titre or resultat.meta_description or resultat.h1 or resultat.alts):
            print("   ✅ Rien à corriger automatiquement sur cette page "
                  f"(problèmes restants : {', '.join(item['problemes'])}).")
            return {"correction": None}   # -> retour à la liste, pas de validation inutile
        return {"correction": resultat.model_dump()}
    except Exception as e:
        print(f"   ⚠️ Erreur IA : {e}")
        return {"correction": None}


def validation(state: AuditState) -> dict:
    """⏸️ Pause : montre la proposition et attend ta décision."""
    item = state["pages_avec_erreurs"][state["page_choisie"]]
    reponse = str(interrupt({
        "type": "validation",
        "page": item["page"],
        "problemes": item["problemes"],
        "correction": state["correction"],
    })).strip().lower()
    return {"decision": reponse if reponse in ("a", "r", "x") else "x"}


def application(state: AuditState) -> dict:
    """Applique les corrections approuvées : sauvegarde JSON + WordPress (simulation ou réel selon WP_MODE)."""
    url = state["pages_avec_erreurs"][state["page_choisie"]]["page"]["url"]
    chemin = sauvegarder(url, Corrections(**state["correction"]))  # dict -> objet Pydantic
    print(f"\n   💾 Approuvé et sauvegardé dans : {chemin}")

    try:
        plan = wordpress.preparer_plan(url, state["correction"])   # lecture seule de l'API
        wordpress.afficher_plan(plan)
        if wordpress.mode() == "reel":
            plan["resultat"] = wordpress.appliquer_plan(plan)      # sauvegarde -> écriture -> vérif
            wordpress.afficher_resultat(plan["resultat"])
        else:
            print("   🧪 SIMULATION : rien n'a été envoyé à WordPress.")
    except Exception as e:
        print(f"   ⚠️ API WordPress injoignable : {e}")
        plan = {"url": url, "erreur": str(e)}

    mise_a_jour = {"sauvegardes": state.get("sauvegardes", []) + [chemin],
                   "plans_wp": state.get("plans_wp", []) + [plan]}
    if wordpress.mode() == "reel":
        mise_a_jour["pages_avec_erreurs"] = reanalyser(state["pages_avec_erreurs"], state["page_choisie"])
    return mise_a_jour


def reanalyser(pages_avec_erreurs: list[dict], index: int) -> list[dict]:
    """Ré-analyse la page corrigée (sans le cache WP Rocket) et renvoie la NOUVELLE liste :
    page retirée si plus aucun problème, sinon ses problèmes restants sont mis à jour."""
    url = pages_avec_erreurs[index]["page"]["url"]
    nouvelle_liste = list(pages_avec_erreurs)   # copie : un nœud ne modifie pas l'état en place
    try:
        page = analyser_page(f"{url}?nocache={int(time.time())}")   # ?nocache contourne le cache
        page["url"] = url
        problemes = detecter_problemes(page)
    except Exception as e:
        print(f"   ⚠️ Ré-analyse impossible ({e}) : la page reste dans la liste.")
        return nouvelle_liste

    if problemes:
        nouvelle_liste[index] = {"page": page, "problemes": problemes}
        print(f"   🔁 Ré-analyse : il reste {len(problemes)} problème(s) : {', '.join(problemes)}")
    else:
        del nouvelle_liste[index]
        print("   🎉 Ré-analyse : plus aucun problème, page retirée de la liste.")
    return nouvelle_liste


# --- 3. Les fonctions de routage ---
def apres_detection(state: AuditState) -> str:
    return "choix_page" if state["pages_avec_erreurs"] else END

def apres_choix(state: AuditState) -> str:
    if state.get("quitter"):
        return END
    return "corrections" if state.get("page_choisie") is not None else "choix_page"

def apres_corrections(state: AuditState) -> str:
    return "validation" if state.get("correction") else "choix_page"

def apres_validation(state: AuditState) -> str:
    return {"a": "application", "r": "corrections"}.get(state["decision"], "choix_page")


# --- 4. Le graphe ---
builder = StateGraph(AuditState)
builder.add_node("audit", audit)
builder.add_node("detection", detection)
builder.add_node("choix_page", choix_page)
builder.add_node("corrections", corrections)
builder.add_node("validation", validation)
builder.add_node("application", application)

builder.add_edge(START, "audit")
builder.add_edge("audit", "detection")
builder.add_conditional_edges("detection", apres_detection, ["choix_page", END])
builder.add_conditional_edges("choix_page", apres_choix, ["corrections", "choix_page", END])
builder.add_conditional_edges("corrections", apres_corrections, ["validation", "choix_page"])
builder.add_conditional_edges("validation", apres_validation, ["application", "corrections", "choix_page"])
builder.add_edge("application", "choix_page")   # retour à la liste

conn = sqlite3.connect("checkpoints.db", check_same_thread=False)   # ← here
graphe = builder.compile(checkpointer=SqliteSaver(conn))   

# --- 5. L'interface terminal : affiche ce que le graphe demande, renvoie ta réponse ---
REPONSES_AUTO = sys.argv[1:]   # pour tester : python graphe_seo.py 2 a q

def demander(question: str) -> str:
    if REPONSES_AUTO:
        reponse = REPONSES_AUTO.pop(0)
        print(f"{question}{reponse}   (auto)")
        return reponse
    return input(question)

def afficher_liste(demande: dict):
    print("\n" + "=" * 60)
    print(f"🚨 {len(demande['pages'])} pages avec des problèmes SEO :\n")
    for p in demande["pages"]:
        print(f"  {p['num']}. {p['url']}  —  {p['nb_problemes']} problème(s)")

def longueur(texte: str, mini: int, maxi: int) -> str:
    """'(57 car.)' si la longueur est bonne, sinon '(163 car. ⚠️ hors 70-160)'."""
    n = len(texte)
    return f"({n} car.)" if mini <= n <= maxi else f"({n} car. ⚠️ hors {mini}-{maxi})"

def afficher_proposition(demande: dict):
    page, c = demande["page"], demande["correction"]
    print(f"\n📄 DÉTAILS : {page['url']}")
    print(f"   Titre : {page['titre']} ({page['longueur_titre']} car.)")
    print(f"   Meta  : {page['meta_description']} ({page['longueur_meta']} car.)")
    print("   🚨 Problèmes :")
    for p in demande["problemes"]:
        print(f"      - {p}")
    print(f"\n✨ CORRECTIONS PROPOSÉES (langue : {c['langue']})")
    hors_limites = False
    if c.get("titre"):
        info = longueur(c["titre"], 30, 60)          # mêmes limites que regles_seo.py
        hors_limites |= "⚠️" in info
        print(f"   Titre : {c['titre']}  {info}")
    if c.get("meta_description"):
        info = longueur(c["meta_description"], 70, 160)
        hors_limites |= "⚠️" in info
        print(f"   Meta  : {c['meta_description']}  {info}")
    if c.get("h1"):
        print(f"   H1    : {c['h1']}")
    for a in c["alts"]:
        print(f"   🖼️ {a['src'].split('/')[-1]}  →  {a['alt']}  {longueur(a['alt'], 5, 125)}")
    if hors_limites:
        print("\n   ⚠️ Une longueur est hors limites : la page resterait en erreur. Conseil : 'r' pour refaire.")


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "audit-1"}}   # l'identifiant de cet audit
    etat = graphe.get_state(config)
    reprendre = False
    if etat.next:   # an audit is waiting for your answer
        nb = len(etat.values.get("pages_avec_erreurs", []))
        print(f"⏯️ Un audit est en pause ({nb} pages dans sa liste, analysées lors de cet ancien audit).")
        choix = demander("   'r' = reprendre cet audit, 'n' = nouvel audit (ré-analyse le site) > ").strip().lower()
        reprendre = choix != "n"
        if not reprendre:
            graphe.checkpointer.delete_thread(config["configurable"]["thread_id"])   # repart de zéro
            print("🗑️ Ancien audit effacé.")
    if reprendre:
        print("⏯️ Reprise de l'audit en pause...")
        resultat = {"__interrupt__": etat.tasks[0].interrupts}
    else:
        resultat = graphe.invoke({
            "sitemap_url": "https://explorawatersports.com/sitemap.xml",
            "max_pages": int(os.getenv("MAX_PAGES", "50")),
        }, config)

    # Tant que le graphe est en pause, on affiche sa demande et on le relance avec la réponse
    while "__interrupt__" in resultat:
        demande = resultat["__interrupt__"][0].value
        if demande["type"] == "choix_page":
            afficher_liste(demande)
            reponse = demander("\nChoisis un numéro (ou 'q' = quitter) > ")
        else:
            afficher_proposition(demande)
            if wordpress.mode() == "reel":
                print("\n   ⚠️ MODE RÉEL : approuver écrit le titre, la meta et les alts sur le site WordPress.")
            reponse = demander("\n   'a' = approuver, 'r' = refaire, 'x' = annuler > ")
        resultat = graphe.invoke(Command(resume=reponse), config)

    print(f"\n👋 Fin. {len(resultat.get('sauvegardes', []))} correction(s) approuvée(s) :")
    for chemin in resultat.get("sauvegardes", []):
        print(f"   - {chemin}")
