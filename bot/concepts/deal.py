"""Zľava týždňa: najznámejšia hra z aktuálnych zliav Steamu (ceny pre Slovensko v €). Cenu, zľavu a platnosť
dopĺňa kód z dát Steamu, Claude píše len o tom, čo je to za hru (z popisu v obchode)."""
from __future__ import annotations

import json
from datetime import date

from .. import sources as src
from ..editor import _effort, _today
from ..llm import STR, STR_LIST, ask_json, schema
from .common import ConceptSkip, build_post, caption_rules, first_image, language, produce_copy, rewrite_note, \
    used_keys

FMT = schema(headline=STR, slides={"type": "array", "items": schema(title=STR, body=STR)}, caption=STR,
             hashtags=STR_LIST, cta_title=STR)
MIN_DISCOUNT = 30
MIN_REVIEWS = 3000  # menej známe hry nechceme, počet recenzií na Steame je dobrá miera známosti


def pick(specials: list[dict], apps: dict, used: set[str]) -> tuple[dict, dict] | None:
    """Najväčší súčin zľavy a známosti (počet recenzií), hra už vyšla a 60 dní nebola zľavou týždňa."""
    best = None
    for it in specials:
        app = apps.get(it["id"])
        if not app or f"deal:{it['id']}" in used or (app.get("release_date") or {}).get("coming_soon"):
            continue
        price = app.get("price_overview") or {}
        reviews = (app.get("recommendations") or {}).get("total", 0)
        if price.get("currency") != "EUR" or price.get("discount_percent", 0) < MIN_DISCOUNT or reviews < MIN_REVIEWS:
            continue
        score = reviews * price["discount_percent"]
        if not best or score > best[0]:
            best = (score, it, app)
    return (best[1], best[2]) if best else None


def make(cfg: dict, posted: list[dict], today: date) -> dict:
    used = used_keys(posted, "deal", 60)
    specials = src.steam_specials()
    apps = {it["id"]: src.steam_app(it["id"]) for it in specials[:30]}
    hit = pick(specials, apps, used)
    if not hit:
        raise ConceptSkip("zľava: žiadna známa hra v zľave")
    item, app = hit
    price = app["price_overview"]
    until = src.until_date(item.get("discount_expiration"))
    name = app.get("name") or item["name"]
    facts = {
        "hra": name, "obchod": "Steam (ceny pre Slovensko)", "cena k dátumu": src.sk_date(today),
        "pôvodná cena": src.euro(price.get("initial")), "cena po zľave": src.euro(price.get("final")),
        "zľava": f"{price['discount_percent']} %", "zľava platí do": src.sk_date(until) if until else "neuvedené",
        "popis z obchodu": app.get("short_description"), "žánre": [g.get("description") for g in app.get("genres") or []],
        "štúdio": app.get("developers"), "vydavateľ": app.get("publishers"),
        "vydanie": (app.get("release_date") or {}).get("date"),
        "Metacritic": (app.get("metacritic") or {}).get("score"),
        "recenzie na Steame": (app.get("recommendations") or {}).get("total"),
    }
    system = f"""{_today()}
Píšeš post "Zľava týždňa" pre slovenskú Instagram stránku o hrách {cfg["brand"]["handle"]}.
Titulku (názov, ceny, zľava, dátum platnosti) dopĺňa kód. Ty píšeš, prečo sa oplatí hru zobrať.

{language(cfg)}

FAKTY
- Píš iba to, čo je v dátach nižšie. Popis z obchodu je reklama vydavateľa: prerozprávaj, čo sa v hre robí,
  bez jeho superlatívov. Nič nedopĺňaj z pamäti (príbeh, hodnotenia, ceny inde).
- Ceny a dátumy presne podľa dát.

ŠTRUKTÚRA
- headline (max 75 znakov): názov, nová cena a zľava ("Hades II je na Steame za 7,49 € so zľavou 75 %").
- slides (2): 1. čo je to za hru (žáner, čo v nej robíš), 2. komu sa bude páčiť a do kedy platí zľava.
  title (max 32 znakov) konkrétny, body (max 220 znakov) celé vety.
- cta_title (max 45 znakov): výzva poslať to kamošovi, ktorý hru ešte nemá.
- {caption_rules(cfg)}"""
    user = "Dáta:\n" + json.dumps(facts, ensure_ascii=False, default=str)

    def write(feedback, previous):
        return ask_json(cfg["model"]["writer"], system, user + rewrite_note(feedback, previous) + "\nNapíš post.",
                        fmt=FMT, effort=_effort(cfg, "write"))

    def validate(c):
        sl = c.get("slides") or []
        issues = []
        if len(sl) != 2 or any(len(s.get("title", "")) > 44 or len(s.get("body", "")) > 300 for s in sl):
            issues.append("slides: presne 2 snímky, title max 32 a body max 220 znakov")
        if not c.get("headline") or len(c["headline"]) > 90:
            issues.append("headline chýba alebo má viac ako 75 znakov")
        if not c.get("cta_title") or len(c["cta_title"]) > 50:
            issues.append("cta_title chýba alebo má viac ako 45 znakov")
        return issues

    copy = produce_copy(write, cfg, validate=validate, facts=facts)
    image = first_image([src.steam_hero(item["id"])] + src.steam_screens(app)[:2])
    until_label = f"zľava do {src.sk_date(until, year=False)}" if until else f"cena k {src.sk_date(today, year=False)}"
    frames = [{"kind": "deal_cover", "image_url": image, "discount": price["discount_percent"], "name": name,
               "price_new": src.euro(price.get("final")), "price_old": src.euro(price.get("initial")),
               "until": f"Steam · {until_label}", "category": "ZĽAVA TÝŽDŇA"}]
    shots = src.steam_screens(app)
    for n, s in enumerate(copy["slides"]):
        shot = shots[n + 1] if len(shots) > n + 1 and len(s["body"]) <= 180 else None
        frames.append({"kind": "text", "title": s["title"], "body": s["body"], "image_url": shot,
                       "photo_credit": "Steam" if shot else None})
    alts = [f"Zľava týždňa: {name} na Steame za {src.euro(price.get('final'))} namiesto "
            f"{src.euro(price.get('initial'))}, zľava {price['discount_percent']} %, {until_label}."] + \
           [f"{s['title']}. {s['body']}" for s in copy["slides"]]
    return build_post("deal", copy, frames, category="ZĽAVA TÝŽDŇA", story=f"Zľava týždňa: {copy['headline']}",
                      key=f"deal:{item['id']}", sources=["Steam"],
                      links=[f"https://store.steampowered.com/app/{item['id']}/"], alts=alts, cta_type="share",
                      photo_credit="Steam", verification=f"Steam, ceny k {src.sk_date(today)}")
