"""Nepopulárny názor: k jednej správe z týždňa ostrejší, ale férový názor stránky (rámec Rant Callout:
tvrdenie, stupňovanie, férový obrat "ale pozor", podpis). Fakty len z pôvodných článkov, názor je označený."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from ..collect import fetch_article
from ..editor import ARTICLE_CHARS, UNTRUSTED, _effort, _today
from ..llm import STR, STR_LIST, ask_json, schema
from .common import ConceptSkip, build_post, caption_rules, language, produce_copy, recent_posts, rewrite_note, \
    used_keys

PICK_FMT = schema(index={"type": "integer"}, angle=STR)
FMT = schema(claim=STR, about=STR, headline=STR, slides={"type": "array", "items": schema(title=STR, body=STR)},
             fair=schema(title=STR, body=STR), caption=STR, hashtags=STR_LIST, cta_title=STR)
RULES = """- názor útočí na konkrétneho človeka, skupinu hráčov alebo na prepustených ľudí (kritizovať sa smú len
  rozhodnutia a praktiky firiem),
- chýba férový obrat (fair), alebo názor nie je zreteľne podaný ako názor stránky."""


def make(cfg: dict, posted: list[dict], today) -> dict:
    used = used_keys(posted, "take", 60)
    week = [p for p in recent_posts(posted, 8, "news") if not p.get("rumor") and p.get("links")
            and f"take:{p['links'][0]}" not in used]
    if len(week) < 3:
        raise ConceptSkip("názor: málo správ z týždňa")
    listing = [{"index": i, "headline": p.get("headline"), "story": p.get("story"), "category": p.get("category")}
               for i, p in enumerate(week)]
    pick = ask_json(cfg["model"]["writer"], f"""{_today()}
Vyberáš tému na post "Nepopulárny názor" slovenskej Instagram stránky o hrách. Hľadáš správu z týždňa,
o ktorej sa medzi hráčmi dá férovo polemizovať: ceny, remastery, mikrotransakcie, predplatné, odklady,
exkluzivity, AI v hrách, rozhodnutia veľkých firiem. Nie tragédie, prepúšťanie ani súdne spory o ľuďoch.
angle = jedna veta, aký ostrejší, ale obhájiteľný názor k nej zaujmeme.""",
                    json.dumps(listing, ensure_ascii=False), fmt=PICK_FMT, effort="low")
    if not 0 <= pick.get("index", -1) < len(week):
        raise ConceptSkip("názor: neplatný výber témy")
    chosen = week[pick["index"]]
    with ThreadPoolExecutor(max_workers=3) as pool:
        articles = list(pool.map(fetch_article, [{"link": link, "source": "", "summary": ""}
                                                 for link in chosen["links"][:3]]))
    articles = [{"link": a["link"], "text": a["text"][:ARTICLE_CHARS]} for a in articles if a.get("text")]
    if not articles:
        raise ConceptSkip("názor: pôvodné články sa nepodarilo stiahnuť")
    system = f"""{_today()}
Píšeš post "Nepopulárny názor" pre slovenskú Instagram stránku o hrách {cfg["brand"]["handle"]}.
Je to označený názor stránky k správe z týždňa. Má byť výrazný, aby sa ľudia v komentároch hádali,
ale férový: kritizuješ rozhodnutia a praktiky firiem, nikdy ľudí.
{UNTRUSTED}

{language(cfg)}

FAKTY
- Každý fakt (čísla, dátumy, mená, čo firma urobila) musí byť v článkoch nižšie. Nič nedopĺňaj z pamäti.
- Názory podávaj ako názory ("podľa mňa", "mne vadí"), nie ako fakty.

ŠTRUKTÚRA (rámec: tvrdenie, stupňovanie, férový obrat)
- claim (max 70 znakov): provokatívne, ale obhájiteľné tvrdenie na titulku (bez "Nepopulárny názor:", to je na štítku).
- about (max 90 znakov): ku ktorej správe sa názor vzťahuje (fakt).
- headline (max 75 znakov): claim a téma pre alt text a notifikácie.
- slides (2 až 3): stupňovanie argumentu, každá snímka konkrétny dôvod s faktom z článkov.
  title (max 32 znakov), body (max 220 znakov).
- fair: férový obrat ("Ale pozor"): čo nekritizuješ a kde má druhá strana pravdu. title max 32, body max 220.
- cta_title (max 45 znakov): otázka, či ľudia súhlasia, odpoveď do komentárov.
- {caption_rules(cfg)}"""
    user = (f"Správa: {chosen.get('headline')}\nUhol názoru: {pick.get('angle')}\n\nČlánky:\n"
            + json.dumps(articles, ensure_ascii=False))

    def write(feedback, previous):
        return ask_json(cfg["model"]["writer"], system, user + rewrite_note(feedback, previous) + "\nNapíš post.",
                        fmt=FMT, effort=_effort(cfg, "write"))

    def validate(c):
        issues = []
        if not c.get("claim") or len(c["claim"]) > 80:
            issues.append("claim chýba alebo má viac ako 70 znakov")
        sl = (c.get("slides") or []) + [c.get("fair") or {}]
        if not 3 <= len(sl) <= 4 or any(not s.get("body") or len(s.get("title", "")) > 44 or len(s["body"]) > 300
                                        for s in sl):
            issues.append("slides 2 až 3 + fair, title max 32 a body max 220 znakov")
        if not c.get("cta_title") or len(c["cta_title"]) > 50:
            issues.append("cta_title chýba alebo má viac ako 45 znakov")
        return issues

    copy = produce_copy(write, cfg, validate=validate, rules=RULES, facts=articles,
                        note="claim a slides obsahujú názor stránky, to je v poriadku, posudzuj fakty v nich.")
    frames = [{"kind": "take_cover", "claim": copy["claim"], "about": copy.get("about")}]
    frames += [{"kind": "text", "title": s["title"], "body": s["body"]} for s in copy["slides"]]
    frames.append({"kind": "text", "label": "Ale pozor", "title": copy["fair"]["title"], "body": copy["fair"]["body"]})
    alts = [f"Nepopulárny názor: {copy['claim']}. {copy.get('about', '')}"] + \
           [f"{s['title']}. {s['body']}" for s in copy["slides"] + [copy["fair"]]]
    return build_post("take", copy, frames, category="NÁZOR", story=f"Názor: {chosen.get('story')}",
                      key=f"take:{chosen['links'][0]}", sources=chosen.get("sources") or [],
                      links=[a["link"] for a in articles], alts=alts, cta_type="comment",
                      verification="názor k overenej správe z týždňa")
