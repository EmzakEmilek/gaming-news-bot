"""Dáta o hrách bez AI: Wikidata (dátum vydania, štúdio, známosť hry), Steam (obrázky, ceny, zľavy)
a Wikipédia (krátke zhrnutie). Všetko verejné API bez kľúča. Fakty v konceptoch (kvíz, retro, zľava)
pochádzajú odtiaľto, Claude ich len prerozpráva."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import quote

import requests

from .common import TZ, log

UA = "EmzoDailyBot/1.0 (https://github.com/EmzakEmilek/gaming-news-bot)"
WIKIDATA = "https://query.wikidata.org/sparql"
STEAM = "https://store.steampowered.com"
STEAM_CDN = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps"
MONTHS = ["januára", "februára", "marca", "apríla", "mája", "júna", "júla", "augusta", "septembra", "októbra",
          "novembra", "decembra"]

# hry (instance of video game) s presným dátumom vydania (presnosť na deň), Steam ID a anglickým článkom
GAMES_QUERY = """
SELECT ?game ?gameLabel ?date ?sl ?steam ?enwiki WHERE {
  ?game wdt:P31 wd:Q7889; wikibase:sitelinks ?sl.
  FILTER(?sl >= %d)
  ?game p:P577/psv:P577 ?tv. ?tv wikibase:timeValue ?date; wikibase:timePrecision 11.
  OPTIONAL { ?game wdt:P1733 ?steam }
  OPTIONAL { ?article schema:about ?game; schema:isPartOf <https://en.wikipedia.org/>; schema:name ?enwiki }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""
DETAILS_QUERY = """
SELECT ?prop ?valLabel WHERE {
  { wd:{qid} wdt:P178 ?val. BIND("developer" AS ?prop) } UNION
  { wd:{qid} wdt:P136 ?val. BIND("genre" AS ?prop) } UNION
  { wd:{qid} wdt:P400 ?val. BIND("platform" AS ?prop) } UNION
  { wd:{qid} wdt:P179 ?val. BIND("series" AS ?prop) }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "sk,cs,en". }
}"""


def sk_date(d: date, year: bool = True) -> str:
    """20. apríla 2006"""
    return f"{d.day}. {MONTHS[d.month - 1]}" + (f" {d.year}" if year else "")


def _get(url: str, params: dict | None = None, timeout: int = 30) -> requests.Response:
    r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": UA, "Accept": "application/json"})
    r.raise_for_status()
    return r


def sparql(query: str) -> list[dict]:
    """Riadky výsledku ako {premenná: hodnota}."""
    r = _get(WIKIDATA, {"query": query, "format": "json"}, timeout=90)
    rows = [{k: v["value"] for k, v in row.items()} for row in r.json()["results"]["bindings"]]
    log.info("Wikidata: %d riadkov (%d kB)", len(rows), len(r.content) // 1024)
    return rows


def parse_games(rows: list[dict]) -> list[dict]:
    """Riadky z GAMES_QUERY -> jedna hra s najskorším dátumom vydania (prvé vydanie, nie port)."""
    games: dict[str, dict] = {}
    for r in rows:
        qid = r["game"].rsplit("/", 1)[-1]
        name = r.get("gameLabel") or ""
        if not name or name == qid:  # bez anglického názvu
            continue
        try:
            d = datetime.fromisoformat(r["date"].replace("Z", "+00:00")).date()
        except ValueError:  # dátumy pred n. l. a podobné výnimky
            continue
        g = games.setdefault(qid, {"qid": qid, "name": name, "first": d, "sitelinks": int(r.get("sl") or 0),
                                   "steam": None, "enwiki": None})
        g["first"] = min(g["first"], d)
        g["steam"] = g["steam"] or r.get("steam")
        g["enwiki"] = g["enwiki"] or r.get("enwiki")
    return sorted(games.values(), key=lambda g: -g["sitelinks"])


def famous_games(min_sitelinks: int = 25) -> list[dict]:
    """Známe hry (článok aspoň v min_sitelinks jazykoch Wikipédie) s dátumom prvého vydania."""
    games = parse_games(sparql(GAMES_QUERY % min_sitelinks))
    log.info("Wikidata: %d známych hier", len(games))
    return games


def game_details(qid: str) -> dict:
    """Štúdiá, žánre, platformy a séria hry (popisy po slovensky, ak existujú)."""
    out: dict[str, list[str]] = {"developer": [], "genre": [], "platform": [], "series": []}
    for r in sparql(DETAILS_QUERY.replace("{qid}", qid)):
        val = r.get("valLabel")
        if val and not val.startswith("Q") and val not in out[r["prop"]]:
            out[r["prop"]].append(val)
    return out


def steam_app(appid: str | int, cc: str = "sk") -> dict | None:
    """Detail hry zo Steamu (názov, popis, obrázky, cena v € pre SK, dátum vydania, štúdiá, žánre)."""
    try:
        data = _get(f"{STEAM}/api/appdetails", {"appids": appid, "cc": cc, "l": "english"}).json()
    except Exception as e:  # noqa: BLE001
        log.warning("Steam appdetails %s zlyhal: %s", appid, e)
        return None
    item = (data or {}).get(str(appid)) or {}
    return item.get("data") if item.get("success") else None


def steam_search(term: str, cc: str = "sk") -> dict | None:
    """Prvý výsledok vyhľadávania v obchode Steam: {"id", "name"}."""
    try:
        items = _get(f"{STEAM}/api/storesearch/", {"term": term, "cc": cc, "l": "english"}).json().get("items") or []
    except Exception as e:  # noqa: BLE001
        log.warning("Steam vyhľadávanie '%s' zlyhalo: %s", term, e)
        return None
    return {"id": items[0]["id"], "name": items[0]["name"]} if items else None


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold().replace("&", "and"))


def steam_find(title: str) -> dict | None:
    """Hra na Steame, len keď sa názov zhoduje (vyhľadávanie inak vráti aj podobnú hru). {"id", "name"}"""
    hit = steam_search(title)
    if not hit:
        return None
    a, b = _norm(title), _norm(hit["name"])
    if a and b and (a == b or (min(len(a), len(b)) >= 6 and (a.startswith(b) or b.startswith(a)))):
        return hit
    log.info("Steam: '%s' nesedí s nájdeným '%s'", title, hit["name"])
    return None


def steam_specials(cc: str = "sk") -> list[dict]:
    """Aktuálne zľavy zo Steamu (výber obchodu): id, name, discount_percent, ceny v centoch, platnosť."""
    data = _get(f"{STEAM}/api/featuredcategories", {"cc": cc, "l": "english"}).json()
    items = (data.get("specials") or {}).get("items") or []
    return [it for it in items if it.get("discounted") and it.get("type", 0) == 0]


def steam_screens(app: dict) -> list[str]:
    """Plné screenshoty hry (1920x1080) zo Steamu."""
    return [s["path_full"] for s in app.get("screenshots") or [] if s.get("path_full")]


def steam_capsule(appid: str | int) -> str:
    """Vysoký plagát hry (600x900 @2x), hodí sa na polovicu snímky VS."""
    return f"{STEAM_CDN}/{appid}/library_600x900_2x.jpg"


def steam_hero(appid: str | int) -> str:
    """Široký obrázok bez loga (library hero 3840x1240)."""
    return f"{STEAM_CDN}/{appid}/library_hero.jpg"


def euro(cents: int | None) -> str:
    """1999 -> 19,99 €"""
    if cents is None:
        return ""
    return f"{cents // 100},{cents % 100:02d} €"


def until_date(ts: int | None) -> date | None:
    """Unix čas konca zľavy -> dátum v slovenskom čase."""
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), timezone.utc).astimezone(TZ).date()


def wiki_summary(title: str) -> dict | None:
    """Úvod článku z anglickej Wikipédie: {"title", "extract", "url"}."""
    try:
        data = _get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}").json()
    except Exception as e:  # noqa: BLE001
        log.warning("Wikipédia '%s' zlyhala: %s", title, e)
        return None
    if not data.get("extract"):
        return None
    return {"title": data.get("title", title), "extract": data["extract"],
            "url": ((data.get("content_urls") or {}).get("desktop") or {}).get("page", "")}
