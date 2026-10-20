"""Vychádza tento týždeň: hry, ktoré vychádzajú od pondelka do nedele. Zdrojom sú týždenné prehľady vydaní
z feedov (PlayStation Blog The Drop, Xbox Wire Next Week on Xbox, Push Square, Nintendo Life...). Každú hru
a jej dátum musí potvrdiť oficiálny zdroj alebo 2 nezávislé portály, rovnako ako pri správach."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from ..collect import collect, fetch_article
from ..common import log
from ..editor import UNTRUSTED, _effort, _today
from ..llm import STR, STR_LIST, ask_json, schema
from ..sources import MONTHS, steam_capsule, steam_find, steam_hero
from .common import WEEKDAYS, ConceptSkip, build_post, caption_rules, check_budget, factcheck, first_image, \
    language, produce_copy, rewrite_note, short_date

LIST_TITLE = re.compile(r"this week|next week|the drop|new games|new releases|releasing|release dates|coming to|"
                        r"vychází|vychádza|tento týden|tento týždeň|tohoto týdne|nové hry|novinky týdne", re.I)
EXTRACT_FMT = schema(games={"type": "array", "items": schema(
    title=STR, date=STR, platforms=STR_LIST, ids=STR_LIST, notable={"type": "integer"}, blurb=STR)})
COPY_FMT = schema(headline=STR, caption=STR, hashtags=STR_LIST, cta_title=STR)
MAX_GAMES = 6
PLATFORM_SHORT = {"PlayStation 5": "PS5", "PlayStation 4": "PS4", "Xbox Series X|S": "Xbox", "Xbox Series X/S": "Xbox",
                  "Xbox One": "Xbox One", "Nintendo Switch 2": "Switch 2", "Nintendo Switch": "Switch", "Windows": "PC"}


def verified(games: list[dict], by_id: dict, start: date, end: date, min_sources: int = 2) -> list[dict]:
    """Hry s dátumom v týždni, ktoré potvrdil oficiálny zdroj alebo min_sources nezávislých portálov."""
    out, seen = [], set()
    for g in games:
        try:
            d = date.fromisoformat(g.get("date", ""))
        except ValueError:
            continue
        arts = [by_id[i] for i in g.get("ids") or [] if i in by_id]
        publishers = {a.get("group") or a["source"] for a in arts}
        official = any(a["tier"] == "official" for a in arts)
        key = g.get("title", "").lower().strip()
        if not key or key in seen or not start <= d <= end or not (official or len(publishers) >= min_sources):
            continue
        seen.add(key)
        out.append({**g, "day": d, "sources": sorted({a["source"] for a in arts})})
    return out


def platforms(names: list[str]) -> list[str]:
    short = [PLATFORM_SHORT.get(n.strip(), n.strip()) for n in names if n and n.strip()]
    short = list(dict.fromkeys(short))
    return short[:3] + ([f"+{len(short) - 3}"] if len(short) > 3 else [])


def _image(title: str) -> str | None:
    hit = steam_find(title)
    return first_image([steam_hero(hit["id"]), steam_capsule(hit["id"])]) if hit else None


def make(cfg: dict, posted: list[dict], today: date) -> dict:
    start, end = today, today + timedelta(days=6)  # najbližších 7 dní (v pondelok = celý týždeň)
    items = [it for it in collect(cfg["feeds"], 96, set()) if LIST_TITLE.search(it["title"])][:14]
    if len(items) < 2:
        raise ConceptSkip("radar: žiadne prehľady vydaní vo feedoch")
    with ThreadPoolExecutor(max_workers=6) as pool:
        articles = list(pool.map(fetch_article, items))
    by_id = {a["id"]: a for a in articles}
    src = [{"id": a["id"], "source": a["source"] + (" (oficiálny)" if a["tier"] == "official" else ""),
            "title": a["title"], "text": a["text"][:5000]} for a in articles]
    check_budget(cfg)
    found = ask_json(cfg["model"]["writer"], f"""{_today()}
Z článkov vyber hry, ktoré vychádzajú (vydanie, nie DLC ani update) od {start.isoformat()} do {end.isoformat()} vrátane.
{UNTRUSTED}
- title: oficiálny názov hry. date: dátum vydania YYYY-MM-DD presne podľa článkov. platforms: platformy podľa článkov.
- ids: VŠETKY id článkov, ktoré túto hru s týmto dátumom uvádzajú.
- notable: 1 až 10, ako veľmi to zaujíma bežného slovenského hráča (známa značka, veľké štúdio, hype).
- blurb (max 110 znakov, po slovensky): čo je to za hru, len podľa článkov. Ak to články neuvádzajú, napíš žáner.
Hry, pri ktorých si dátumom nie si istý, vynechaj.""", "Články:\n" + json.dumps(src, ensure_ascii=False),
                    fmt=EXTRACT_FMT, effort=_effort(cfg, "select"))
    log.info("Radar: z %d prehľadov (%s) vytiahnuté hry: %s", len(articles), ", ".join(a["source"] for a in articles),
             [(g.get("title"), g.get("date"), [by_id[i]["source"] for i in g.get("ids") or [] if i in by_id])
              for g in found.get("games") or []])
    games = verified(found.get("games") or [], by_id, start, end)
    games = sorted(sorted(games, key=lambda g: -g.get("notable", 0))[:MAX_GAMES], key=lambda g: g["day"])
    if len(games) < 3:
        raise ConceptSkip(f"radar: overené len {len(games)} hry")
    facts = [{"title": g["title"], "date": g["date"], "platforms": g.get("platforms"), "blurb": g.get("blurb"),
              "articles": [s for s in src if s["id"] in (g.get("ids") or [])]} for g in games]
    blocking = factcheck({"games": [{k: g[k] for k in ("title", "date", "platforms", "blurb")} for g in games]},
                         facts, cfg, note="Over názov, dátum vydania, platformy a popis každej hry.")
    if blocking:
        raise ConceptSkip(f"radar: zoznam neprešiel kontrolou faktov: {blocking}")
    sub = f"{start.day}. – {end.day}. {MONTHS[end.month - 1]}" if start.month == end.month else \
        f"{start.day}. {MONTHS[start.month - 1]} – {end.day}. {MONTHS[end.month - 1]}"
    listing = [{"title": g["title"], "date": g["date"], "platforms": g.get("platforms"), "blurb": g.get("blurb")}
               for g in games]
    system = f"""{_today()}
Píšeš caption k postu "Vychádza tento týždeň" ({sub}) pre slovenskú Instagram stránku o hrách {cfg["brand"]["handle"]}.
Zoznam hier na snímkach dopĺňa kód, ty píšeš len headline (max 75 znakov, napr. "Vychádza tento týždeň: X, Y a ďalšie"),
caption a cta_title (max 45 znakov, výzva uložiť si to alebo napísať, čo si zoberie). Fakty len zo zoznamu.

{language(cfg)}
{caption_rules(cfg)}"""

    def write(feedback, previous):
        return ask_json(cfg["model"]["writer"], system, json.dumps(listing, ensure_ascii=False)
                        + rewrite_note(feedback, previous), fmt=COPY_FMT, effort="low")

    def validate(c):
        bad = not c.get("cta_title") or len(c["cta_title"]) > 50 or not c.get("headline") or len(c["headline"]) > 90
        return ["headline max 75 a cta_title max 45 znakov"] if bad else []

    copy = produce_copy(write, cfg, validate=validate, facts=listing)
    frames = [{"kind": "list_cover", "category": "VYCHÁDZA TENTO TÝŽDEŇ", "title_main": "Čo budeš", "title_hl": "hrať",
               "sub": sub, "teaser": [{"label": g["title"], "chip": WEEKDAYS[g["day"].weekday()]} for g in games]}]
    with ThreadPoolExecutor(max_workers=6) as pool:  # obrázky hier zo Steamu (len pri presnej zhode názvu)
        images = list(pool.map(_image, [g["title"] for g in games]))
    for n, (g, img) in enumerate(zip(games, images), start=1):
        frames.append({"kind": "list_item", "n": n, "title": g["title"], "body": g.get("blurb", ""),
                       "chips": [short_date(g["day"])] + platforms(g.get("platforms") or []),
                       "image_url": img, "photo_credit": "Steam" if img else None})
    alts = [f"Vychádza tento týždeň ({sub}): " + "; ".join(f"{g['title']} ({short_date(g['day'])})" for g in games)] + \
           [f"{g['title']}, {short_date(g['day'])}, {', '.join(g.get('platforms') or [])}. {g.get('blurb', '')}"
            for g in games]
    return build_post("radar", copy, frames, category="VYCHÁDZA", story=f"Vychádza tento týždeň {sub}",
                      key=f"radar:{start.isoformat()}", sources=sorted({s for g in games for s in g["sources"]}),
                      links=[a["link"] for a in articles if any(a["id"] in (g.get("ids") or []) for g in games)],
                      alts=alts, cta_type="save", verification="oficiálny zdroj alebo 2 portály pri každej hre")
