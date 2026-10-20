"""Pred X rokmi: výročie vydania známej hry (Wikidata), fakty z úvodu článku na Wikipédii, obrázky zo Steamu."""
from __future__ import annotations

import json
from datetime import date

from .. import sources as src
from ..editor import _effort, _today
from ..llm import STR, STR_LIST, ask_json, schema
from .common import ConceptSkip, build_post, caption_rules, first_image, language, produce_copy, rewrite_note, \
    used_keys

FMT = schema(slides={"type": "array", "items": schema(title=STR, body=STR)}, caption=STR, hashtags=STR_LIST,
             cta_title=STR)
WINDOW_DAYS = 6  # výročie, ktoré nastalo dnes alebo za posledných 6 dní (sobota pokryje celý týždeň)
MIN_YEARS = 5


def anniversary(game: dict, today: date) -> int | None:
    """Koľko rokov od prvého vydania, ak výročie nastalo dnes alebo za posledných WINDOW_DAYS dní, inak None.
    Len minulé výročia: "pred 25 rokmi" musí platiť presne (budúce výročie by bolo o rok menej)."""
    first = game["first"]
    for year in (today.year, today.year - 1):  # začiatok januára: výročie mohlo byť koncom decembra
        try:
            day = first.replace(year=year)
        except ValueError:  # 29. február
            day = date(year, 3, 1)
        years = year - first.year
        if 0 <= (today - day).days <= WINDOW_DAYS and years >= MIN_YEARS:
            return years
    return None


def pick(games: list[dict], used: set[str], today: date) -> tuple[dict, int] | None:
    """Okrúhle výročie (10, 15, 20... rokov) má prednosť, potom známosť hry."""
    hits = [(g, y) for g in games if f"retro:{g['qid']}" not in used and g.get("enwiki")
            and (y := anniversary(g, today))]
    if not hits:
        return None
    return max(hits, key=lambda gy: (gy[1] % 5 == 0, gy[0]["sitelinks"]))


def make(cfg: dict, posted: list[dict], today: date) -> dict:
    used = used_keys(posted, "retro", 3 * 365)
    hit = pick(src.famous_games(20), used, today)
    if not hit:
        raise ConceptSkip("retro: žiadne výročie známej hry")
    game, years = hit
    wiki = src.wiki_summary(game["enwiki"])
    if not wiki:
        raise ConceptSkip(f"retro: chýba článok o {game['name']}")
    details = src.game_details(game["qid"])
    facts = {"dnes": src.sk_date(today), "hra": game["name"], "prvé vydanie": src.sk_date(game["first"]),
             "pred rokmi": years,
             "štúdio": details.get("developer"), "séria": details.get("series"),
             "platformy (všetky vrátane neskorších portov)": details.get("platform"),
             "Wikipédia": wiki["extract"]}
    system = f"""{_today()}
Píšeš retro carousel "Pred {years} rokmi" pre slovenskú Instagram stránku o hrách {cfg["brand"]["handle"]}.
Titulku (rok, názov, dátum) dopĺňa kód. Ty píšeš obsahové snímky a caption pre hráča, ktorý hru možno hral
ako decko, alebo o nej len počul. Nostalgia, ale fakty presne.

{language(cfg)}

FAKTY
- Píš iba to, čo je v dátach nižšie (Wikipédia a údaje o hre). Nič nedopĺňaj z pamäti.
- Platformy v dátach sú aj neskoršie porty, nepíš, na čom hra pôvodne vyšla, ak to Wikipédia neuvádza.

ŠTRUKTÚRA
- slides (2 až 3): 1. čo to bola za hru a prečo stojí za spomienku, 2. zaujímavosť alebo prečo bola výnimočná
  (z textu Wikipédie), 3. voliteľne čo z nej ostalo (pokračovania, remaster), ak to dáta uvádzajú.
  title (max 32 znakov) konkrétny, body (max 220 znakov) celé vety.
- cta_title (max 45 znakov): výzva poslať post kamošovi, s ktorým to hral, alebo spomienka do komentárov.
- {caption_rules(cfg)}"""
    user = "Dáta:\n" + json.dumps(facts, ensure_ascii=False, default=str)

    def write(feedback, previous):
        c = ask_json(cfg["model"]["writer"], system, user + rewrite_note(feedback, previous) + "\nNapíš post.",
                     fmt=FMT, effort=_effort(cfg, "write"))
        c["headline"] = f"Pred {years} rokmi vyšla hra {game['name']}"
        return c

    def validate(c):
        sl = c.get("slides") or []
        bad = not 2 <= len(sl) <= 3 or any(len(s.get("title", "")) > 44 or len(s.get("body", "")) > 300 for s in sl)
        return (["slides: 2 až 3 snímky, title max 32 a body max 220 znakov"] if bad else []) + \
               (["cta_title chýba alebo má viac ako 45 znakov"] if not c.get("cta_title") or len(c["cta_title"]) > 50
                else [])

    copy = produce_copy(write, cfg, validate=validate, facts=facts)
    image = None
    if game.get("steam"):
        app = src.steam_app(game["steam"]) or {}
        image = first_image([src.steam_hero(game["steam"])] + src.steam_screens(app)[:2])
    frames = [{"kind": "retro_cover", "image_url": image, "years": years, "year": game["first"].year,
               "name": game["name"], "release_label": src.sk_date(game["first"])}]
    frames += [{"kind": "text", "title": s["title"], "body": s["body"]} for s in copy["slides"]]
    alts = [f"Retro: pred {years} rokmi, {src.sk_date(game['first'])}, vyšla hra {game['name']}."] + \
           [f"{s['title']}. {s['body']}" for s in copy["slides"]]
    return build_post("retro", copy, frames, category="RETRO", story=copy["headline"], key=f"retro:{game['qid']}",
                      sources=["Wikipédia", "Wikidata"] + (["Steam"] if image else []), links=[wiki["url"]],
                      alts=alts, cta_type="share", photo_credit="Steam" if image else None,
                      verification="Wikipédia a Wikidata")
