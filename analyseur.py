# analyseur.py — Outil qui analyse le SEO d'une page web
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

FORMATS_IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def src_image(img, url: str) -> str | None:
    """URL absolue d'une image de CONTENU (jpg/png/webp/gif), ou None.
    Gère le lazy-loading WordPress ; ignore les SVG (logo, drapeaux WPML) et les data:."""
    src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
    if not src or src.startswith("data:"):
        return None
    src = urljoin(url, src)
    return src if urlparse(src).path.lower().endswith(FORMATS_IMAGES) else None


def images_contenu_sans_alt(soup: BeautifulSoup, url: str) -> list[str]:
    """URLs des images de CONTENU sans alt (sans doublons).
    Règle unique, utilisée aussi par correcteur.py et wordpress.py -> tout le monde compte pareil."""
    images = []
    for img in soup.find_all("img"):
        if img.get("alt", "").strip():
            continue
        src = src_image(img, url)
        if src and src not in images:
            images.append(src)
    return images


def analyser_page(url: str) -> dict:
    """Récupère et analyse les éléments SEO d'une page web."""
    # 1. Télécharger la page
    headers = {"User-Agent": "Mozilla/5.0"}  # pour ne pas être bloqué
    reponse = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(reponse.text, "lxml")

    # 2. Extraire les éléments SEO clés
    titre = soup.title.string.strip() if soup.title else "❌ MANQUANT"

    meta_desc = soup.find("meta", attrs={"name": "description"})
    meta_desc = meta_desc["content"].strip() if meta_desc else "❌ MANQUANTE"

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = [h.get_text(strip=True) for h in soup.find_all("h2")]

    # Images de contenu sans attribut alt (mauvais pour le SEO)
    images = soup.find_all("img")
    img_sans_alt = images_contenu_sans_alt(soup, url)

    # Nombre de mots dans le texte
    texte = soup.get_text()
    nb_mots = len(texte.split())

    # 3. Retourner un résumé structuré
    return {
        "titre": titre,
        "longueur_titre": len(titre),
        "meta_description": meta_desc,
        "longueur_meta": len(meta_desc),
        "nombre_h1": len(h1s),
        "h1": h1s,
        "nombre_h2": len(h2s),
        "total_images": len(images),
        "images_sans_alt": len(img_sans_alt),
        "nombre_mots": nb_mots,
    }

# Test direct
if __name__ == "__main__":
    import json
    url = "https://explorawatersports.com"   # ton site !
    resultat = analyser_page(url)
    print(json.dumps(resultat, indent=2, ensure_ascii=False))