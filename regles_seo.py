# regles_seo.py — Règles SEO : détecte les problèmes d'une page analysée
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
