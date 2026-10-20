"""Uhádneš hru? Výrez zo screenshotu známej hry sa s každou indíciou zväčšuje, odpoveď je na konci.
Bez Claude: hra je z Wikidata (známosť = počet jazykov Wikipédie), obrázky a žáner zo Steamu, indície skladá kód."""
from __future__ import annotations

import random

from .. import sources as src
from .common import ConceptSkip, build_post, used_keys

STEAM_GENRES = {"Action": "akčná", "Adventure": "adventúra", "RPG": "RPG", "Strategy": "stratégia",
                "Simulation": "simulátor", "Sports": "športová", "Racing": "preteky", "Indie": "indie",
                "Casual": "oddychová", "Massively Multiplayer": "MMO"}
CAPTIONS = [
    "Uhádneš hru len zo screenshotu? Každá snímka pridá indíciu a ukáže viac.\n\n"
    "Tip napíš do komentárov skôr, ako uvidíš odpoveď. Odpoveď je na poslednej snímke, nepodvádzaj.",
    "Toto poznáš, len si na to spomenúť. Swipuj, s každou snímkou uvidíš viac.\n\n"
    "Napíš do komentárov, na koľkej snímke si to mal.",
    "Kvíz pre tých, čo už hrali všeličo. Pár pohľadov, pár indícií, jedna hra.\n\n"
    "Daj tip do komentárov a pošli to kamošovi, nech sa potrápi aj on.",
]
HASHTAGS = ["#hry", "#hernykviz", "#uhadnihru", "#videohry"]
MIN_SITELINKS = 45


def pick(games: list[dict], used: set[str], today) -> dict | None:
    """Známa hra so Steamom, staršia ako rok, ktorá v kvíze ešte nebola. Náhodne, aby to nebola stále tá najznámejšia."""
    pool = [g for g in games if g.get("steam") and f"quiz:{g['qid']}" not in used
            and 1995 <= g["first"].year and (today - g["first"]).days > 365][:400]
    return random.Random(today.isoformat()).choice(pool) if pool else None


def clues(game: dict, app: dict, details: dict) -> list[str]:
    """Tri indície od najťažšej: rok, žáner, štúdio. Žiadna nesmie prezradiť názov hry."""
    out = [f"Vyšla v roku {game['first'].year}"]
    genres = [STEAM_GENRES[g["description"]] for g in app.get("genres") or [] if g.get("description") in STEAM_GENRES]
    platforms = details.get("platform") or []
    if genres:
        out.append("Žáner: " + ", ".join(dict.fromkeys(genres[:2])))
    elif platforms:
        out.append("Hrá sa na: " + ", ".join(platforms[:3]))
    devs = app.get("developers") or details.get("developer") or []
    if devs:
        out.append(f"Vyvinulo ju štúdio {devs[0]}")
    name_words = {w.lower() for w in game["name"].replace(":", " ").split() if len(w) > 3}
    return [c for c in out if not name_words & {w.lower().strip(",.") for w in c.split()}]


def make(cfg: dict, posted: list[dict], today) -> dict:
    used = used_keys(posted, "quiz", 365)
    game = pick(src.famous_games(MIN_SITELINKS), used, today)
    if not game:
        raise ConceptSkip("kvíz: žiadna vhodná hra")
    app = src.steam_app(game["steam"])
    screens = src.steam_screens(app or {})
    if not app or len(screens) < 2:
        raise ConceptSkip(f"kvíz: {game['name']} nemá na Steame screenshoty")
    details = src.game_details(game["qid"])
    hints = clues(game, app, details)
    if len(hints) < 2:
        raise ConceptSkip(f"kvíz: málo indícií k {game['name']}")
    rnd = random.Random(game["qid"])
    shot = screens[min(len(screens) - 1, rnd.randint(1, 3))]  # prvý screenshot býva s logom hry
    cx, cy = rnd.uniform(.35, .65), rnd.uniform(.35, .6)
    zooms = [4.0, 3.0, 2.0, 1.4][: len(hints) + 1]
    frames = [{"kind": "quiz_cover", "image_url": shot, "crop": {"zoom": zooms[0], "cx": cx, "cy": cy},
               "sub": f"{len(hints)} indície, odpoveď na konci. Koľko ich potrebuješ?"}]
    for n, (hint, zoom) in enumerate(zip(hints, zooms[1:]), start=1):
        frames.append({"kind": "quiz_clue", "image_url": shot, "crop": {"zoom": zoom, "cx": cx, "cy": cy},
                       "clue": hint, "clue_n": n, "clue_total": len(hints)})
    devs = app.get("developers") or details.get("developer") or []
    facts = " · ".join([x for x in (devs[0] if devs else "", str(game["first"].year)) if x])
    frames.append({"kind": "quiz_reveal", "image_url": shot, "name": game["name"], "facts": facts})
    caption = random.Random(today.isoformat()).choice(CAPTIONS)
    copy = {"headline": f"Kvíz: uhádneš hru? (odpoveď: {game['name']})", "caption": caption, "hashtags": HASHTAGS,
            "cta_title": "Na koľkej snímke si to uhádol?"}
    alts = ["Kvíz: Uhádneš hru? Priblížený výrez zo screenshotu hry."] + \
           [f"Indícia {n}: {h}. Výrez je menej priblížený." for n, h in enumerate(hints, start=1)] + \
           [f"Odpoveď: {game['name']}, {facts}."]
    return build_post("quiz", copy, frames, category="KVÍZ", story=f"Kvíz: {game['name']}", key=f"quiz:{game['qid']}",
                      sources=["Steam", "Wikidata"], links=[f"https://www.wikidata.org/wiki/{game['qid']}"],
                      alts=alts, cta_type="comment", photo_credit="Steam")
