# sitemap.py — Audite toutes les pages d'un site à partir de son sitemap
import requests
from bs4 import BeautifulSoup
from analyseur import analyser_page

HEADERS = {"User-Agent": "Mozilla/5.0"}


def lire_sitemap(sitemap_url: str) -> list[str]:
    """Retourne la liste des URLs d'un sitemap (gère aussi les index de sitemaps)."""
    reponse = requests.get(sitemap_url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(reponse.content, "xml")

    # Cas 1 : index de sitemaps -> on lit chaque sous-sitemap
    sous_sitemaps = [loc.text.strip() for loc in soup.select("sitemap > loc")]
    if sous_sitemaps:
        urls = []
        for s in sous_sitemaps:
            urls.extend(lire_sitemap(s))
        return urls

    # Cas 2 : sitemap classique
    return [loc.text.strip() for loc in soup.select("url > loc")]


def auditer_site(sitemap_url: str, max_pages: int = 50) -> list:
    """Analyse le SEO de chaque page du sitemap (limité à max_pages)."""
    urls = lire_sitemap(sitemap_url)[:max_pages]
    resultats = []
    for url in urls:
        try:
            analyse = analyser_page(url)
            analyse["url"] = url
        except Exception as e:
            analyse = {"url": url, "erreur": str(e)}
        resultats.append(analyse)
    return resultats


# Test direct
if __name__ == "__main__":
    import json
    res = auditer_site("https://explorawatersports.com/sitemap.xml", max_pages=5)
    print(json.dumps(res, indent=2, ensure_ascii=False))
