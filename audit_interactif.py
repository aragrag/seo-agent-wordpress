# audit_interactif.py — Audit du site + menu + corrections IA
import time
from sitemap import lire_sitemap
from analyseur import analyser_page
from correcteur import generer_corrections, sauvegarder


# --- Règles SEO : détecte les problèmes d'une page ---
def detecter_problemes(page: dict) -> list:
    problemes = []
    t, lt = page.get("titre", ""), page.get("longueur_titre", 0)
    if t == "❌ MANQUANT":            problemes.append("Titre manquant")
    elif lt > 60:                     problemes.append(f"Titre trop long ({lt} car.)")
    elif lt < 30:                     problemes.append(f"Titre trop court ({lt} car.)")

    md, lm = page.get("meta_description", ""), page.get("longueur_meta", 0)
    if md == "❌ MANQUANTE":          problemes.append("Meta description manquante")
    elif lm > 160:                    problemes.append(f"Meta trop longue ({lm} car.)")
    elif lm < 70:                     problemes.append(f"Meta trop courte ({lm} car.)")

    h1 = page.get("nombre_h1", 0)
    if h1 == 0:                       problemes.append("Aucun H1")
    elif h1 > 1:                      problemes.append(f"Plusieurs H1 ({h1})")

    if page.get("images_sans_alt", 0) > 0:
        problemes.append(f"{page['images_sans_alt']} image(s) sans alt")
    if page.get("nombre_mots", 0) < 300:
        problemes.append(f"Contenu court ({page.get('nombre_mots')} mots)")
    return problemes


# --- 1. Auditer tout le site ---
sitemap_url = "https://explorawatersports.com/sitemap.xml"
print("🔍 Lecture du sitemap...")
urls = lire_sitemap(sitemap_url)[:50]
print(f"📄 {len(urls)} pages à analyser\n")

pages_avec_erreurs = []
for i, url in enumerate(urls, 1):
    print(f"  [{i}/{len(urls)}] {url}")
    try:
        data = analyser_page(url)
        data["url"] = url
        probs = detecter_problemes(data)
        if probs:
            pages_avec_erreurs.append((data, probs))
    except Exception as e:
        print(f"     ⚠️ {e}")
    time.sleep(0.3)

# --- 2. Liste des pages avec erreurs ---
def afficher_liste():
    print("\n" + "=" * 60)
    print(f"🚨 {len(pages_avec_erreurs)} pages avec des problèmes SEO :\n")
    for i, (page, probs) in enumerate(pages_avec_erreurs, 1):
        print(f"  {i}. {page['url']}  —  {len(probs)} problème(s)")

afficher_liste()

# --- 3. Menu interactif ---
while True:
    choix = input("\nChoisis un numéro (ou 'l' = liste, 'q' = quitter) > ").strip()
    if choix.lower() in ("q", "quit", "exit"):
        print("À bientôt ! 👋")
        break
    if choix.lower() == "l":
        afficher_liste()
        continue
    if not choix.isdigit() or not (1 <= int(choix) <= len(pages_avec_erreurs)):
        print("❌ Numéro invalide.")
        continue

    page, probs = pages_avec_erreurs[int(choix) - 1]
    print(f"\n📄 DÉTAILS : {page['url']}")
    print(f"   Titre      : {page['titre']} ({page['longueur_titre']} car.)")
    print(f"   Meta       : {page['meta_description']} ({page['longueur_meta']} car.)")
    print(f"   H1 : {page['nombre_h1']}  |  H2 : {page['nombre_h2']}")
    print(f"   Images     : {page['total_images']} dont {page['images_sans_alt']} sans alt")
    print(f"   Mots       : {page['nombre_mots']}")
    print(f"\n   🚨 Problèmes :")
    for p in probs:
        print(f"      - {p}")

    # --- 4. Corrections par IA ---
    sous_choix = input("\n   Tape 'c' pour générer les corrections IA (ou Entrée) > ").strip()
    if sous_choix.lower() == "c":
        print("\n   ⏳ L'IA analyse la page et les images...")
        try:
            corr = generer_corrections(page, probs)
            print(f"\n✨ CORRECTIONS (langue : {corr.langue})\n")
            if corr.titre:
                print(f"   Titre : {corr.titre}  ({len(corr.titre)} car.)")
            if corr.meta_description:
                print(f"   Meta  : {corr.meta_description}  ({len(corr.meta_description)} car.)")
            if corr.h1:
                print(f"   H1    : {corr.h1}")
            if corr.alts:
                print("\n   🖼️ Textes alt proposés :")
                for a in corr.alts:
                    print(f"      {a.src.split('/')[-1]}  →  {a.alt}")
            chemin = sauvegarder(page["url"], corr)
            print(f"\n   💾 Sauvegardé dans : {chemin}")
        except Exception as e:
            print(f"   ⚠️ Erreur IA : {e}")