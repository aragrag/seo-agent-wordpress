# wordpress.py — Lien avec l'API REST de WordPress
# Étape 4a : plan en SIMULATION (lecture seule)
# Étape 4b : mode RÉEL pour les alts d'images (sauvegarde -> écriture -> vérification)
# Étape 4c : mode RÉEL pour le titre et la meta Yoast (nécessite le snippet PHP sur le site)
#
# Mode choisi par la variable WP_MODE : "simulation" (défaut) ou "reel"
# Annuler une modification :  python wordpress.py restaurer backups/<fichier>.json
import os
import re
import json
from datetime import datetime
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from analyseur import src_image

load_dotenv()

WP_URL = os.getenv("WP_URL", "https://explorawatersports.com").rstrip("/")
API = f"{WP_URL}/wp-json/wp/v2"
HEADERS = {"User-Agent": "Mozilla/5.0"}
DOSSIER_BACKUPS = "backups"


def mode() -> str:
    return "reel" if os.getenv("WP_MODE", "simulation").lower() == "reel" else "simulation"


def _auth() -> tuple[str, str]:
    """Identifiants lus dans .env (jamais écrits dans le code)."""
    user, pwd = os.getenv("WP_USER"), os.getenv("WP_APP_PASSWORD")
    if not (user and pwd):
        raise RuntimeError("WP_USER / WP_APP_PASSWORD manquants dans .env")
    return user, pwd


def _meme_url(a: str, b: str) -> bool:
    return a.rstrip("/").lower() == b.rstrip("/").lower()


# --- 1. Trouver l'article / la page WordPress derrière une URL ---
def trouver_contenu(url: str, langue: str | None = None) -> dict | None:
    """Renvoie {"id", "type", "link", "titre_actuel", "meta_actuelle"} ou None.
    Cherche par slug dans les articles puis les pages, et vérifie que le lien correspond
    exactement (avec WPML, le même slug peut exister dans plusieurs langues)."""
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    if not slug:
        return None   # page d'accueil : cas particulier, non géré pour l'instant
    for type_wp in ("posts", "pages"):
        for params_lang in ({}, {"lang": langue} if langue else None):
            if params_lang is None:
                continue
            r = requests.get(f"{API}/{type_wp}", headers=HEADERS, timeout=15,
                             params={"slug": slug, "_fields": "id,link,yoast_head_json", **params_lang})
            if not r.ok:
                continue
            for item in r.json():
                if _meme_url(item["link"], url):
                    yoast = item.get("yoast_head_json") or {}
                    return {"id": item["id"], "type": type_wp, "link": item["link"],
                            "titre_actuel": yoast.get("title"),
                            "meta_actuelle": yoast.get("description")}
    return None


# --- 2. Trouver l'ID WordPress de chaque image (classe "wp-image-NNNN") ---
def trouver_ids_images(url: str) -> dict[str, int]:
    """{url de l'image: id du média} pour les images de contenu de la page."""
    soup = BeautifulSoup(requests.get(url, headers=HEADERS, timeout=15).text, "lxml")
    ids = {}
    for img in soup.find_all("img"):
        src = src_image(img, url)
        m = re.search(r"wp-image-(\d+)", " ".join(img.get("class", [])))
        if src and m:
            ids[src] = int(m.group(1))
    return ids


def lire_alt(media_id: int) -> str | None:
    r = requests.get(f"{API}/media/{media_id}", headers=HEADERS, timeout=15,
                     params={"_fields": "id,alt_text"})
    return r.json().get("alt_text", "") if r.ok else None


# --- 3. Préparer le plan : ce qu'on enverrait, avec l'ancienne valeur ---
def preparer_plan(url: str, correction: dict) -> dict:
    """Lecture seule. Décrit chaque modification : cible API, ancienne valeur, nouvelle valeur."""
    plan = {"url": url, "contenu": None, "modifications": [], "ignorees": []}

    contenu = trouver_contenu(url, correction.get("langue"))
    plan["contenu"] = contenu

    if correction.get("titre") or correction.get("meta_description"):
        if contenu:
            cible = f"{contenu['type']}/{contenu['id']}"
            if correction.get("titre"):
                plan["modifications"].append({"champ": "titre (Yoast)", "cible": cible,
                    "ancien": contenu["titre_actuel"], "nouveau": correction["titre"]})
            if correction.get("meta_description"):
                plan["modifications"].append({"champ": "meta (Yoast)", "cible": cible,
                    "ancien": contenu["meta_actuelle"], "nouveau": correction["meta_description"]})
        else:
            plan["ignorees"].append("titre/meta : article ou page introuvable dans l'API")

    if correction.get("h1"):
        plan["ignorees"].append(f"H1 « {correction['h1']} » : dans la mise en page Elementor, à faire à la main")

    if correction.get("alts"):
        ids = trouver_ids_images(url)
        for a in correction["alts"]:
            media_id = ids.get(a["src"])
            if media_id is None:
                plan["ignorees"].append(f"alt de {a['src'].split('/')[-1]} : ID du média introuvable")
                continue
            plan["modifications"].append({"champ": "alt image", "cible": f"media/{media_id}",
                "ancien": lire_alt(media_id), "nouveau": a["alt"]})
    return plan


def afficher_plan(plan: dict):
    c = plan["contenu"]
    print(f"\n   🌐 WordPress : {c['type']}/{c['id']}" if c else "\n   🌐 WordPress : contenu introuvable")
    for m in plan["modifications"]:
        print(f"   • {m['champ']}  →  POST /wp/v2/{m['cible']}")
        print(f"       avant : {m['ancien']!r}")
        print(f"       après : {m['nouveau']!r}")
    for raison in plan["ignorees"]:
        print(f"   ⏭️ ignoré : {raison}")


# --- 4. Mode RÉEL : sauvegarde -> écriture -> vérification ---
YOAST_CHAMPS = {"titre (Yoast)": "_yoast_wpseo_title", "meta (Yoast)": "_yoast_wpseo_metadesc"}


def lire_meta_yoast(cible: str) -> dict:
    """Valeurs BRUTES des champs Yoast (ex. cible = "posts/2617").
    Échoue clairement si le snippet PHP n'est pas actif (champs absents de l'API)."""
    r = requests.get(f"{API}/{cible}", params={"context": "edit", "_fields": "meta"},
                     auth=_auth(), headers=HEADERS, timeout=15)
    r.raise_for_status()
    meta = r.json().get("meta") or {}
    manquants = [k for k in YOAST_CHAMPS.values() if k not in meta]
    if manquants:
        raise RuntimeError("champs Yoast non accessibles par l'API (snippet PHP pas actif ?) : "
                           + ", ".join(manquants))
    return {k: meta[k] for k in YOAST_CHAMPS.values()}


def _ecrire_meta_yoast(cible: str, valeurs: dict) -> dict:
    """Écrit les champs Yoast puis les relit. Renvoie les valeurs relues."""
    r = requests.post(f"{API}/{cible}", json={"meta": valeurs},
                      auth=_auth(), headers=HEADERS, timeout=20)
    r.raise_for_status()
    return lire_meta_yoast(cible)


def metas_description_publiques(url: str) -> list[str]:
    """Contenu de chaque <meta name="description"> de la page publique (sans cache).
    Plus d'une = doublon (Yoast + un autre plugin)."""
    soup = BeautifulSoup(requests.get(url, headers=HEADERS, timeout=20,
                                      params={"nocache": datetime.now().strftime("%H%M%S")}).text, "lxml")
    return [m.get("content", "") for m in soup.find_all("meta", attrs={"name": "description"})]


def _ecrire_alt(media_id: int, alt: str) -> str:
    """Écrit l'alt puis le relit avec authentification pour vérifier. Renvoie la valeur relue."""
    r = requests.post(f"{API}/media/{media_id}", json={"alt_text": alt},
                      auth=_auth(), headers=HEADERS, timeout=20)
    r.raise_for_status()
    relu = requests.get(f"{API}/media/{media_id}", params={"context": "edit", "_fields": "alt_text"},
                        auth=_auth(), headers=HEADERS, timeout=15)
    relu.raise_for_status()
    return relu.json()["alt_text"]


def appliquer_plan(plan: dict) -> dict:
    """Applique le plan : alts d'images + titre/meta Yoast.
    Ordre : lecture des valeurs brutes -> sauvegarde -> écriture -> relecture."""
    alts = [m for m in plan["modifications"] if m["champ"] == "alt image"]
    yoast = [m for m in plan["modifications"] if m["champ"] in YOAST_CHAMPS]
    resultat = {"backup": None, "appliquees": [], "echecs": [], "metas_publiques": None}

    # 0. Valeurs Yoast BRUTES actuelles (pour la sauvegarde) ; vérifie aussi que le snippet est actif
    if yoast:
        try:
            bruts = lire_meta_yoast(yoast[0]["cible"])
            for m in yoast:
                m["ancien_brut"] = bruts[YOAST_CHAMPS[m["champ"]]]
        except Exception as e:
            resultat["echecs"].append(f"titre/meta Yoast non envoyés : {e}")
            yoast = []
    if not (alts or yoast):
        return resultat

    # 1. Sauvegarde des anciennes valeurs
    os.makedirs(DOSSIER_BACKUPS, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", urlparse(plan["url"]).path.lower()).strip("-") or "accueil"
    chemin = os.path.join(DOSSIER_BACKUPS, f"{datetime.now():%Y%m%d-%H%M%S}_{slug}.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump({"url": plan["url"], "date": datetime.now().isoformat(timespec="seconds"),
                   "modifications": alts + yoast}, f, ensure_ascii=False, indent=2)
    resultat["backup"] = chemin

    # 2. Écriture + 3. vérification : alts, une image à la fois
    for m in alts:
        media_id = int(m["cible"].split("/")[1])
        try:
            relu = _ecrire_alt(media_id, m["nouveau"])
            if relu == m["nouveau"]:
                resultat["appliquees"].append(f"alt ({m['cible']})")
            else:
                resultat["echecs"].append(f"alt ({m['cible']}) : valeur relue différente ({relu!r})")
        except Exception as e:
            resultat["echecs"].append(f"alt ({m['cible']}) : {e}")

    # 2. Écriture + 3. vérification : titre + meta Yoast, en une seule requête
    if yoast:
        cible = yoast[0]["cible"]
        try:
            relu = _ecrire_meta_yoast(cible, {YOAST_CHAMPS[m["champ"]]: m["nouveau"] for m in yoast})
            for m in yoast:
                valeur = relu[YOAST_CHAMPS[m["champ"]]]
                if valeur == m["nouveau"]:
                    resultat["appliquees"].append(f"{m['champ']} ({cible})")
                else:
                    resultat["echecs"].append(f"{m['champ']} ({cible}) : valeur relue différente ({valeur!r})")
        except Exception as e:
            resultat["echecs"].append(f"titre/meta Yoast ({cible}) : {e}")
        # 4. Contrôle de la page publique : y a-t-il plusieurs meta description ?
        try:
            resultat["metas_publiques"] = metas_description_publiques(plan["url"])
        except Exception:
            pass
    return resultat


def afficher_resultat(res: dict):
    if res["backup"]:
        print(f"   🗂️ Sauvegarde des anciennes valeurs : {res['backup']}")
    for cible in res["appliquees"]:
        print(f"   ✅ Écrit et vérifié : {cible}")
    for e in res["echecs"]:
        print(f"   ❌ Échec : {e}")
    metas = res.get("metas_publiques")
    if metas and len(metas) > 1:
        print(f"   ⚠️ La page publique a {len(metas)} balises <meta name=\"description\"> :")
        for contenu in metas:
            print(f"        - {contenu!r}")
        print("      -> une vient d'un autre plugin que Yoast : il faut la retirer.")


def restaurer(chemin_backup: str):
    """Remet les anciennes valeurs enregistrées dans un fichier de sauvegarde."""
    with open(chemin_backup, encoding="utf-8") as f:
        backup = json.load(f)
    yoast = {}
    for m in backup["modifications"]:
        if m["champ"] == "alt image":
            media_id = int(m["cible"].split("/")[1])
            relu = _ecrire_alt(media_id, m["ancien"] or "")
            print(f"   ↩️ alt {m['cible']} restauré : {relu!r}")
        elif m["champ"] in YOAST_CHAMPS:
            yoast.setdefault(m["cible"], {})[YOAST_CHAMPS[m["champ"]]] = m.get("ancien_brut") or ""
    for cible, valeurs in yoast.items():
        relu = _ecrire_meta_yoast(cible, valeurs)
        print(f"   ↩️ Yoast {cible} restauré : {relu}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "restaurer":
        restaurer(sys.argv[2])
    else:
        print("Usage : python wordpress.py restaurer backups/<fichier>.json")
