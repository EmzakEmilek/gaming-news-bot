"""Toto alebo toto: dve hry (alebo dva spôsoby hrania) proti sebe, ľudia hlasujú v komentároch 1 alebo 2.
Bez faktov: argumenty sú názory, fakty (čísla, dátumy, funkcie) čitateľská kontrola zablokuje."""
from __future__ import annotations

import json

from .. import sources as src
from ..editor import _effort, _today
from ..llm import STR, STR_LIST, ask_json, schema
from .common import ConceptSkip, build_post, caption_rules, first_image, language, produce_copy, recent_posts, \
    rewrite_note, used_keys

SIDE = schema(name=STR, steam_search=STR)
FMT = schema(key=STR, question=STR, a=SIDE, b=SIDE, points_a=STR_LIST, points_b=STR_LIST, headline=STR,
             caption=STR, hashtags=STR_LIST, cta_title=STR)
RULES = """- argument (points_a, points_b) tvrdí konkrétny fakt: číslo, dátum, cenu, predaj, hodnotenie, dĺžku hry
  alebo konkrétnu funkciu či obsah hry, ktorý by mohol byť nepravdivý. Argumenty musia byť dojmy a dôvody voľby,
- otázka alebo dvojica nedáva zmysel, možnosti nie sú porovnateľné alebo je jedna jasne lepšia."""


def _validate(c: dict) -> list[str]:
    issues = []
    if not c.get("question") or len(c["question"]) > 70:
        issues.append("question chýba alebo má viac ako 60 znakov")
    for side in ("a", "b"):
        name = (c.get(side) or {}).get("name") or ""
        if not name or len(name) > 32:
            issues.append(f"{side}.name chýba alebo má viac ako 28 znakov")
    for key in ("points_a", "points_b"):
        pts = c.get(key) or []
        if not 2 <= len(pts) <= 3 or any(not p or len(p) > 70 for p in pts):
            issues.append(f"{key}: 2 až 3 argumenty, každý najviac 60 znakov")
    if not c.get("cta_title") or len(c["cta_title"]) > 45:
        issues.append("cta_title chýba alebo má viac ako 45 znakov")
    if not c.get("headline") or len(c["headline"]) > 90:
        issues.append("headline chýba alebo má viac ako 75 znakov")
    return issues


def _image(term: str) -> str | None:
    """Plagát hry zo Steamu (vysoký formát), inak široký obrázok alebo screenshot."""
    if not term:
        return None
    hit = src.steam_find(term)
    if not hit:
        return None
    url = first_image([src.steam_capsule(hit["id"]), src.steam_hero(hit["id"])])
    if url:
        return url
    app = src.steam_app(hit["id"]) or {}
    return first_image(src.steam_screens(app)[:2])


def make(cfg: dict, posted: list[dict], today) -> dict:
    used = used_keys(posted, "vs", 120)
    context = [p.get("headline") for p in recent_posts(posted, 14) if p.get("concept", "news") == "news"][-20:]
    system = f"""{_today()}
Pripravuješ post "Toto alebo toto" pre slovenskú Instagram stránku o hrách {cfg["brand"]["handle"]}.
Na titulke sú dve možnosti proti sebe a ľudia v komentároch hlasujú 1 alebo 2.

{language(cfg)}

VÝBER DVOJICE
- Dve porovnateľné, známe veci, o ktorých má názor bežný slovenský hráč: dve hry toho istého žánru alebo série,
  dve konzoly, dva spôsoby hrania (fyzická kópia alebo digitál, gamepad alebo myš). Ideálne naviazané
  na aktuálne správy (zoznam nižšie), aby to bolo aktuálne. Bez ľudí, politiky a citlivých tém.
- Obe možnosti musia mať svojich fanúšikov, aby sa ľudia hádali.
- key: krátky identifikátor dvojice (napr. "gta6-vs-rdr2"), nesmie byť medzi už použitými.

TEXTY
- question (max 60 znakov): otázka na titulku ("Na čo si radšej zoberieš dovolenku?").
- a/b.name (max 28 znakov): presný oficiálny názov. steam_search: názov hry na vyhľadanie obrázka na Steame,
  prázdne, ak to nie je hra na PC (konzola, Nintendo exkluzivita, spôsob hrania).
- points_a, points_b: 2 až 3 dôvody, prečo by si človek vybral túto možnosť (každý max 60 znakov). Sú to dojmy
  a pocity ("príbeh, na ktorý sa nezabúda"), NIKDY nie fakty (čísla, dátumy, ceny, predaje, hodnotenia, dĺžka,
  konkrétne funkcie). Férovo, obe strany rovnako dobre.
- headline (max 75 znakov): "A alebo B?" pre alt text a notifikácie.
- cta_title (max 45 znakov): výzva hlasovať v komentároch (1 alebo 2).
- {caption_rules(cfg)}"""
    user = (f"Aktuálne správy (inšpirácia): {json.dumps(context, ensure_ascii=False)}\n"
            f"Už použité dvojice: {json.dumps(sorted(used), ensure_ascii=False)}\nPriprav post.")

    def write(feedback, previous):
        c = ask_json(cfg["model"]["writer"], system, user + rewrite_note(feedback, previous), fmt=FMT,
                     effort=_effort(cfg, "write"))
        if f"vs:{c.get('key')}" in used:
            c["key"] = c["key"] + "-2"
        return c

    copy = produce_copy(write, cfg, validate=_validate, rules=RULES)
    a, b = copy["a"], copy["b"]
    img_a, img_b = _image(a.get("steam_search", "")), _image(b.get("steam_search", ""))
    if not img_a and not img_b and (a.get("steam_search") or b.get("steam_search")):
        raise ConceptSkip("VS: obrázky hier sa nepodarilo nájsť")
    frames = [
        {"kind": "vs_cover", "question": copy["question"], "a": {"name": a["name"], "image_url": img_a},
         "b": {"name": b["name"], "image_url": img_b}, "credit": "Foto: Steam" if img_a or img_b else ""},
        {"kind": "vs_side", "side": 1, "title": f"Za {a['name']}", "points": copy["points_a"], "image_url": img_a},
        {"kind": "vs_side", "side": 2, "title": f"Za {b['name']}", "points": copy["points_b"], "image_url": img_b},
    ]
    alts = [f"Toto alebo toto: {copy['question']} 1: {a['name']}, 2: {b['name']}.",
            f"Za {a['name']}: " + "; ".join(copy["points_a"]), f"Za {b['name']}: " + "; ".join(copy["points_b"])]
    return build_post("vs", copy, frames, category="TOTO ALEBO TOTO", story=f"Toto alebo toto: {copy['headline']}",
                      key=f"vs:{copy['key']}", sources=[], links=[], alts=alts, cta_type="comment",
                      photo_credit="Steam" if img_a or img_b else None)
