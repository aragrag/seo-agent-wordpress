# maillage.py — Audit du maillage interne (LECTURE SEULE, rien n'est modifié)
# Analyse les liens placés DANS LE CONTENU (pas le menu ni le pied de page,
# qui sont identiques partout et n'aident pas à hiérarchiser les pages).
#
# Lancer :  python maillage.py      -> affiche le résumé + écrit maillage_rapport.json
import json
from collections import defaultdict
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import wordpress as wp

TYPES = {                      # rest_base -> rôle
    "posts": "article",
    "pages": "page",
    "activities": "activité",      # pages commerciales
    "holidays": "séjour",          # pages commerciales
    "accomondations": "hébergement",
}
COMMERCIALES = {"activité", "séjour"}
DOMAINES = {"explorawatersports.com", "www.explorawatersports.com"}
LANGUES = {"fr", "es", "nl", "de"}


def normaliser(href: str, base: str) -> str | None:
    """URL interne propre (sans ?query ni #ancre, avec / final), ou None si externe / fichier."""
    u = urlparse(urljoin(base, href))
    if u.scheme not in ("http", "https") or u.netloc.lower() not in DOMAINES:
        return None
    if u.path.startswith(("/wp-content/", "/wp-admin/", "/wp-json/")):
        return None
    chemin = u.path if u.path.endswith("/") else u.path + "/"
    return f"https://explorawatersports.com{chemin}"


def langue(url: str) -> str:
    seg = urlparse(url).path.strip("/").split("/")[0]
    return seg if seg in LANGUES else "en"


def recuperer_contenus() -> list[dict]:
    """Tous les contenus publiés, avec leur HTML rendu."""
    contenus = []
    for base, role in TYPES.items():
        page = 1
        while True:
            r = requests.get(f"{wp.API}/{base}", auth=wp._auth(), headers=wp.HEADERS, timeout=30,
                             params={"per_page": 100, "page": page, "lang": "all", "status": "publish",
                                     "_fields": "id,link,title,content"})
            if not r.ok:
                break
            lot = r.json()
            for it in lot:
                contenus.append({"id": it["id"], "type": base, "role": role,
                                 "url": normaliser(it["link"], it["link"]) or it["link"],
                                 "titre": BeautifulSoup(it["title"]["rendered"], "lxml").get_text(),
                                 "html": it["content"]["rendered"] or ""})
            if len(lot) < 100:
                break
            page += 1
    return contenus


def auditer() -> dict:
    contenus = recuperer_contenus()
    index = {c["url"]: c for c in contenus}
    entrants = defaultdict(set)          # url cible -> urls sources
    for c in contenus:
        soup = BeautifulSoup(c["html"], "lxml")
        c["nb_mots"] = len(soup.get_text(" ").split())
        c["analysable"] = c["nb_mots"] >= 30  # sinon contenu Elementor/JetEngine, non lisible ici
        cibles = set()
        for a in soup.find_all("a", href=True):
            cible = normaliser(a["href"], c["url"])
            if cible and cible != c["url"]:
                cibles.add(cible)
        c["sortants"] = sorted(cibles)
        for cible in cibles:
            entrants[cible].add(c["url"])

    # Liens internes vers des URLs inconnues : on vérifie si elles répondent
    inconnues = sorted({t for c in contenus for t in c["sortants"] if t not in index})
    statut = {}
    for u in inconnues:
        try:
            statut[u] = requests.head(u, headers=wp.HEADERS, timeout=15, allow_redirects=True).status_code
        except Exception:
            statut[u] = None

    lignes = []
    for c in contenus:
        sortants = c["sortants"]
        lignes.append({
            "url": c["url"], "id": c["id"], "type": c["type"], "role": c["role"],
            "titre": c["titre"], "langue": langue(c["url"]), "nb_mots": c["nb_mots"],
            "analysable": c["analysable"],
            "liens_sortants": len(sortants),
            "vers_commerciales": sum(1 for t in sortants if t in index and index[t]["role"] in COMMERCIALES),
            "liens_entrants": len(entrants.get(c["url"], ())),
            "autre_langue": [t for t in sortants if langue(t) != langue(c["url"])],
            "casses": [t for t in sortants if t not in index and statut.get(t) not in (200, None)],
        })
    return {"contenus": lignes, "inconnues": statut}


def afficher(rapport: dict):
    L = rapport["contenus"]
    print(f"\n📊 {len(L)} contenus analysés")
    for role in ("article", "page", "activité", "séjour", "hébergement"):
        n = [x for x in L if x["role"] == role]
        if n:
            print(f"   {role:12} : {len(n)}")

    articles = [x for x in L if x["role"] == "article" and x["analysable"]]
    print("\n🔗 ARTICLES : liens internes dans le texte")
    for x in sorted(articles, key=lambda x: (x["liens_sortants"], x["vers_commerciales"])):
        alerte = " ⚠️ aucun lien vers une activité/séjour" if x["vers_commerciales"] == 0 else ""
        print(f"   {x['liens_sortants']:2} liens ({x['vers_commerciales']} commerciaux) | "
              f"{x['nb_mots']:4} mots | [{x['langue']}] {x['titre'][:60]}{alerte}")

    print("\n💰 PAGES COMMERCIALES : combien de contenus les citent ?")
    for x in sorted([x for x in L if x["role"] in COMMERCIALES], key=lambda x: x["liens_entrants"]):
        alerte = "  ⚠️ orpheline" if x["liens_entrants"] == 0 else ""
        print(f"   {x['liens_entrants']:2} lien(s) entrant(s) | [{x['langue']}] {x['role']:8} {x['titre'][:55]}{alerte}")

    croises = [(x, t) for x in L for t in x["autre_langue"]]
    if croises:
        print(f"\n🌍 {len(croises)} lien(s) vers une AUTRE langue (ex. article FR -> page EN) :")
        for x, t in croises[:10]:
            print(f"   [{x['langue']}] {x['titre'][:40]}  ->  {t}")

    casses = [(x, t) for x in L for t in x["casses"]]
    if casses:
        print(f"\n❌ {len(casses)} lien(s) interne(s) cassé(s) ou redirigé(s) vers une erreur :")
        for x, t in casses:
            print(f"   {x['titre'][:40]}  ->  {t}  (statut {rapport['inconnues'].get(t)})")

    non_lus = [x for x in L if not x["analysable"]]
    print(f"\nℹ️ {len(non_lus)} contenu(s) construits avec Elementor/JetEngine : liens sortants non analysés "
          f"(leurs liens ENTRANTS sont bien comptés).")


if __name__ == "__main__":
    rapport = auditer()
    afficher(rapport)
    with open("maillage_rapport.json", "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2)
    print("\n💾 Rapport complet : maillage_rapport.json")
