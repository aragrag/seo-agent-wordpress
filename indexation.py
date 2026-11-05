# indexation.py — Audit d'indexation du site (LECTURE SEULE, rien n'est modifié)
# Détecte 404, redirections, noindex, blocages robots.txt, canonicals, pages orphelines, contenu faible.
#
# Deux modes :
#   python indexation.py              -> basé sur le SITEMAP (défaut) : analyse les URLs du sitemap,
#                                        et vérifie juste le statut des liens internes hors sitemap
#   python indexation.py --complet    -> exploration complète, suit tous les liens (max 800 URLs)
# Résultat : résumé à l'écran + indexation_rapport.json
import sys
import json
import time
from collections import defaultdict, deque
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from sitemap import lire_sitemap

SITE = "https://explorawatersports.com"
DOMAINES = {"explorawatersports.com", "www.explorawatersports.com"}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ExploraSEOAudit/1.0)"}
EXTENSIONS_FICHIERS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".pdf", ".zip",
                       ".mp4", ".css", ".js", ".xml", ".ico", ".woff", ".woff2")
MOTS_MIN = 300


def normaliser(href: str, base: str) -> str | None:
    """URL interne de page HTML, sans ?query ni #ancre. None si externe ou fichier."""
    u = urlparse(urljoin(base, href.strip()))
    if u.scheme not in ("http", "https") or u.netloc.lower() not in DOMAINES:
        return None
    if u.path.lower().endswith(EXTENSIONS_FICHIERS) or u.path.startswith(("/wp-content/", "/wp-json/")):
        return None
    return f"{SITE}{u.path or '/'}"


def analyser_html(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    robots = " ".join(m.get("content", "") for m in soup.find_all("meta", attrs={"name": "robots"})).lower()
    canon = soup.find("link", rel="canonical")
    liens = {l for a in soup.find_all("a", href=True) if (l := normaliser(a["href"], url))}
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return {
        "titre": soup.title.get_text(strip=True) if soup.title else "",
        "noindex_meta": "noindex" in robots,
        "canonical": urljoin(url, canon["href"]) if canon and canon.get("href") else None,
        "nb_mots": len(soup.get_text(" ").split()),
        "liens": liens,
    }


def _avec_reessais(fonction, *args, essais: int = 3):
    """Relance une fonction si la connexion est coupée (attente 5 s, 10 s...)."""
    for n in range(1, essais + 1):
        try:
            return fonction(*args)
        except requests.exceptions.ConnectionError:
            if n == essais:
                raise
            print(f"   ⚠️ connexion coupée, nouvel essai dans {5 * n} s...")
            time.sleep(5 * n)


def crawler(max_pages: int = 800, sitemap_seul: bool = True) -> dict:
    robots = RobotFileParser(f"{SITE}/robots.txt")
    _avec_reessais(robots.read)
    sitemap = {normaliser(u, SITE) for u in _avec_reessais(lire_sitemap, f"{SITE}/sitemap.xml")} - {None}

    session = requests.Session()
    session.headers.update(HEADERS)
    # Réessaie automatiquement les connexions coupées et les erreurs 429/5xx passagères
    reessais = Retry(total=3, connect=3, read=3, backoff_factor=2,
                     status_forcelist=(429, 502, 503, 504), allowed_methods=("GET", "HEAD"),
                     respect_retry_after_header=True)
    session.mount("https://", HTTPAdapter(max_retries=reessais))
    pages, entrants = {}, defaultdict(set)
    a_visiter = deque(sorted(sitemap) if sitemap_seul else [f"{SITE}/", *sorted(sitemap)])
    vus = set(a_visiter)

    while a_visiter and len(pages) < max_pages:
        url = a_visiter.popleft()
        info = {"url": url, "dans_sitemap": url in sitemap,
                "bloque_robots": not robots.can_fetch(HEADERS["User-Agent"], url)}
        try:
            r = session.get(url, timeout=20, allow_redirects=False)
            info["statut"] = r.status_code
            info["noindex_entete"] = "noindex" in r.headers.get("X-Robots-Tag", "").lower()
            if r.is_redirect:
                info["redirige_vers"] = urljoin(url, r.headers.get("Location", ""))
                cible = normaliser(info["redirige_vers"], url)
                if not sitemap_seul and cible and cible not in vus:
                    vus.add(cible)
                    a_visiter.append(cible)
            elif r.ok and "text/html" in r.headers.get("Content-Type", ""):
                data = analyser_html(url, r.text)
                for lien in data.pop("liens"):
                    if lien != url:
                        entrants[lien].add(url)
                    if not sitemap_seul and lien not in vus:
                        vus.add(lien)
                        a_visiter.append(lien)
                info.update(data)
        except Exception as e:
            info["statut"] = None
            info["erreur"] = str(e)
        pages[url] = info
        print(f"  [{len(pages)}] {info.get('statut')} {url}")
        time.sleep(0.4)   # rythme doux : évite d'être pris pour une attaque par Wordfence

    # Mode sitemap : les liens internes HORS sitemap ne sont pas explorés,
    # on vérifie seulement leur statut (404 ? redirection ?) avec une requête légère.
    if sitemap_seul:
        hors_sitemap = sorted(set(entrants) - set(pages))
        print(f"\n🔎 Vérification rapide de {len(hors_sitemap)} liens internes hors sitemap...")
        for url in hors_sitemap:
            info = {"url": url, "dans_sitemap": False, "hors_sitemap": True,
                    "bloque_robots": not robots.can_fetch(HEADERS["User-Agent"], url)}
            try:
                r = session.head(url, timeout=20, allow_redirects=False)
                if r.status_code == 405:   # HEAD refusé -> GET
                    r = session.get(url, timeout=20, allow_redirects=False)
                info["statut"] = r.status_code
                info["noindex_entete"] = "noindex" in r.headers.get("X-Robots-Tag", "").lower()
                if r.is_redirect:
                    info["redirige_vers"] = urljoin(url, r.headers.get("Location", ""))
            except Exception as e:
                info["statut"] = None
                info["erreur"] = str(e)
            pages[url] = info
            print(f"  [hors sitemap] {info.get('statut')} {url}")
            time.sleep(0.1)
        a_visiter.clear()

    for url, info in pages.items():
        info["liens_entrants"] = sorted(entrants.get(url, ()))
    return {"pages": pages, "non_explorees": len(a_visiter), "taille_sitemap": len(sitemap)}


def classer(resultat: dict) -> dict:
    P = resultat["pages"]
    def sources(u): return P[u]["liens_entrants"]
    ok = [p for p in P.values() if p.get("statut") == 200]
    return {
        "404": [{"url": u, "liee_depuis": sources(u), "dans_sitemap": p["dans_sitemap"]}
                for u, p in P.items() if p.get("statut") == 404],
        "erreurs_serveur": [{"url": u, "statut": p.get("statut")} for u, p in P.items()
                            if p.get("statut") is None or p.get("statut", 0) >= 500],
        "redirections": [{"url": u, "vers": p["redirige_vers"], "statut": p["statut"],
                          "liee_depuis": sources(u), "dans_sitemap": p["dans_sitemap"]}
                         for u, p in P.items() if p.get("redirige_vers")],
        "noindex": [{"url": u, "titre": p.get("titre"), "dans_sitemap": p["dans_sitemap"],
                     "via": "meta" if p.get("noindex_meta") else "en-tête"}
                    for u, p in P.items() if p.get("noindex_meta") or p.get("noindex_entete")],
        "bloquees_robots": [{"url": u, "liee_depuis": sources(u)} for u, p in P.items() if p["bloque_robots"]],
        "canonical_ailleurs": [{"url": p["url"], "canonical": p["canonical"], "dans_sitemap": p["dans_sitemap"]}
                               for p in ok if p.get("canonical")
                               and normaliser(p["canonical"], p["url"]) != p["url"]],
        "orphelines": [{"url": p["url"], "titre": p.get("titre")} for p in ok
                       if p["dans_sitemap"] and not p["liens_entrants"]
                       and not (p.get("noindex_meta") or p.get("noindex_entete"))],
        # seulement les pages dont le contenu a été lu (pas les liens hors sitemap juste vérifiés)
        "contenu_faible": sorted([{"url": p["url"], "titre": p.get("titre"), "nb_mots": p["nb_mots"]}
                                  for p in ok if "nb_mots" in p and p["nb_mots"] < MOTS_MIN
                                  and not (p.get("noindex_meta") or p.get("noindex_entete"))],
                                 key=lambda x: x["nb_mots"]),
    }


def afficher(resultat: dict, classes: dict):
    P = resultat["pages"]
    print("\n" + "=" * 70)
    print(f"🕷️ {len(P)} URLs explorées | sitemap : {resultat['taille_sitemap']} URLs | "
          f"non explorées (limite atteinte) : {resultat['non_explorees']}")
    titres = {
        "404": "❌ 404 (Not found)",
        "erreurs_serveur": "💥 Erreurs serveur / injoignables",
        "redirections": "↪️ Redirections (Page with redirect)",
        "noindex": "🚫 noindex (Excluded by noindex)",
        "bloquees_robots": "🤖 Bloquées par robots.txt",
        "canonical_ailleurs": "🔀 Canonical vers une autre URL",
        "orphelines": "🏝️ Orphelines : dans le sitemap, aucun lien interne (→ Discovered not indexed)",
        "contenu_faible": f"📄 Contenu faible < {MOTS_MIN} mots (→ Crawled not indexed)",
    }
    for cle, titre in titres.items():
        items = classes[cle]
        print(f"\n{titre} : {len(items)}")
        for it in items[:12]:
            extra = ""
            if "liee_depuis" in it:
                extra += f" | liée depuis {len(it['liee_depuis'])} page(s)"
            if it.get("dans_sitemap"):
                extra += " | ⚠️ DANS LE SITEMAP"
            if "vers" in it:
                extra += f" | -> {it['vers']}"
            if "nb_mots" in it:
                extra += f" | {it['nb_mots']} mots"
            if "canonical" in it:
                extra += f" | canonical -> {it['canonical']}"
            print(f"   {it['url']}{extra}")
        if len(items) > 12:
            print(f"   … et {len(items) - 12} autres (voir indexation_rapport.json)")


if __name__ == "__main__":
    complet = "--complet" in sys.argv
    print("Mode :", "exploration complète" if complet else "basé sur le sitemap")
    resultat = crawler(800, sitemap_seul=not complet)
    classes = classer(resultat)
    afficher(resultat, classes)
    with open("indexation_rapport.json", "w", encoding="utf-8") as f:
        json.dump({"resume": {k: len(v) for k, v in classes.items()}, "problemes": classes,
                   "pages": resultat["pages"]}, f, ensure_ascii=False, indent=2)
    print("\n💾 Rapport complet : indexation_rapport.json")
