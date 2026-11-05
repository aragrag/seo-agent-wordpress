# correcteur.py — Corrections SEO par IA : langue de la page + alts par vision + JSON
import os, json, re
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage
from analyseur import images_contenu_sans_alt

load_dotenv()

llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
    api_version="2024-10-21",
)

HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_IMAGES = 5   # limite les coûts : 5 images max par page


# --- Le format de sortie attendu (= ce qu'on enverra à WordPress plus tard) ---
class AltImage(BaseModel):
    src: str = Field(description="URL de l'image")
    alt: str = Field(description="Texte alt descriptif, dans la langue de la page, max 125 caractères")

class Corrections(BaseModel):
    langue: str = Field(description="Code langue de la page, ex: fr, en, es, nl")
    titre: str | None = Field(default=None, description="Titre SEO optimisé, 50-60 caractères, ou null si non demandé")
    meta_description: str | None = Field(default=None, description="Meta description optimisée, 140-160 caractères, ou null si non demandée")
    h1: str | None = Field(default=None, description="H1 suggéré si la page n'en a pas, sinon null")
    alts: list[AltImage] = Field(default_factory=list)


# --- Outils de lecture de la page ---
def recuperer_page(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    return BeautifulSoup(resp.text, "lxml")

def detecter_langue(soup: BeautifulSoup, url: str) -> str:
    """Lit <html lang="..."> ; sinon devine via l'URL (/fr/, /es/, /nl/)."""
    lang = soup.html.get("lang") if soup.html else None
    if lang:
        return lang.split("-")[0].lower()
    segment = urlparse(url).path.strip("/").split("/")[0]
    return segment if segment in ("fr", "es", "nl", "de", "en") else "en"

def images_sans_alt(soup: BeautifulSoup, url: str) -> list:
    """Images de contenu sans alt (même règle que analyseur.py), limitées à MAX_IMAGES."""
    return images_contenu_sans_alt(soup, url)[:MAX_IMAGES]

def extraire_texte(soup: BeautifulSoup, max_chars: int = 2000) -> str:
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())[:max_chars]


# --- La génération ---
def generer_corrections(page: dict, problemes: list) -> Corrections:
    soup = recuperer_page(page["url"])
    langue = detecter_langue(soup, page["url"])
    images = images_sans_alt(soup, page["url"])   # avant extraire_texte (qui modifie soup)
    texte = extraire_texte(soup)

    # L'IA ne corrige QUE ce qui a été détecté comme problème
    corriger_titre = any(p.startswith("Titre") for p in problemes)
    corriger_meta = any(p.startswith("Meta") for p in problemes)
    corriger_h1 = "Aucun H1" in problemes

    if not (corriger_titre or corriger_meta or corriger_h1 or images):
        return Corrections(langue=langue)   # rien de corrigeable automatiquement -> pas d'appel IA

    taches = []
    if corriger_titre: taches.append("- titre: optimized SEO title, 50-60 characters")
    if corriger_meta:  taches.append("- meta_description: optimized meta description, 140-160 characters, compelling")
    if corriger_h1:    taches.append("- h1: a suggested H1 for the page")
    if images:         taches.append("- alts: one alt per image listed below (max 125 chars), with src = the exact image URL given")

    consignes = f"""You are an SEO expert. Fix ONLY the fields listed below.
Return null for titre, meta_description and h1 when they are not listed, and an empty alts list when no image is listed.
{chr(10).join(taches)}

IMPORTANT: The page language is "{langue}". Write ALL corrections in "{langue}". Never translate into another language.

URL: {page['url']}
Current title: {page['titre']} ({page['longueur_titre']} chars)
Current meta description: {page['meta_description']} ({page['longueur_meta']} chars)
Number of H1: {page['nombre_h1']}
Detected problems:
{chr(10).join('- ' + p for p in problemes)}

Page content (excerpt):
{texte}"""

    def appeler(avec_vision: bool) -> Corrections:
        contenu = [{"type": "text", "text": consignes}]
        for src in images:
            contenu.append({"type": "text", "text": f"Image: {src}"})
            if avec_vision:
                contenu.append({"type": "image_url", "image_url": {"url": src, "detail": "low"}})
        return llm.with_structured_output(Corrections).invoke([HumanMessage(content=contenu)])

    try:
        resultat = appeler(avec_vision=bool(images))
    except Exception as e:
        if not images:
            raise
        # Plan B : si la vision échoue (image inaccessible, etc.), alts basés sur le nom de fichier + contexte
        print(f"   ⚠️ Vision indisponible ({e.__class__.__name__}), alts basés sur le nom de fichier.")
        resultat = appeler(avec_vision=False)

    # Garde-fous : on ne fait pas confiance à l'IA, on filtre dans le code
    if not corriger_titre: resultat.titre = None
    if not corriger_meta:  resultat.meta_description = None
    if not corriger_h1:    resultat.h1 = None
    resultat.alts = [a for a in resultat.alts if a.src in images]   # supprime les alts inventés
    resultat.langue = langue
    return resultat


def sauvegarder(page_url: str, corrections: Corrections, dossier: str = "corrections") -> str:
    """Sauvegarde en JSON : c'est ce fichier qu'on enverra à WordPress plus tard."""
    os.makedirs(dossier, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", urlparse(page_url).path.lower()).strip("-") or "accueil"
    chemin = os.path.join(dossier, f"{slug}.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump({"url": page_url, **corrections.model_dump()}, f, ensure_ascii=False, indent=2)
    return chemin