"""Týždeň v správach: 5 najdôležitejších správ, ktoré stránka za týždeň postla (už overené),
každá na jednej snímke. Rámec Value-Stack: presný počet na titulke, každá snímka ho spláca, uloženie na konci."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from ..common import TZ
from ..editor import _effort, _today
from ..llm import STR, STR_LIST, ask_json, schema
from ..sources import MONTHS
from .common import ConceptSkip, build_post, caption_rules, language, produce_copy, recent_posts, rewrite_note, \
    short_date

FMT = schema(items={"type": "array", "items": schema(index={"type": "integer"}, label=STR, title=STR, body=STR)},
             headline=STR, caption=STR, hashtags=STR_LIST, cta_title=STR)
COUNT = 5


def make(cfg: dict, posted: list[dict], today: date) -> dict:
    week = [p for p in recent_posts(posted, 7, "news") if p.get("headline")]
    count = min(COUNT, len(week))
    if count < 4:
        raise ConceptSkip("týždeň: málo správ")
    listing = [{"index": i, "date": p["date"], "hook": p.get("hook"), "headline": p["headline"], "story": p.get("story"),
                "facts": p.get("facts"), "rumor": p.get("rumor", False)} for i, p in enumerate(week)]
    system = f"""{_today()}
Pripravuješ nedeľný post "{count} správ týždňa" pre slovenskú Instagram stránku o hrách {cfg["brand"]["handle"]}.
Dostaneš správy, ktoré stránka za týždeň postla (fakty sú overené). Vyber {count} najdôležitejších
a najzaujímavejších pre bežného hráča, každá o inej udalosti, zoradené od najväčšej.

{language(cfg)}

FAKTY
- Píš len to, čo je v headline, story a facts danej správy. Nič nedopĺňaj z pamäti. Fámy (rumor) ostanú fámami
  ("vraj", "podľa insidera").

ŠTRUKTÚRA
- items: presne {count}. index = číslo správy zo zoznamu. label (max 36 znakov): krátke pomenovanie na titulku.
  title (max 40 znakov): čo sa stalo. body (max 160 znakov): prečo je to dôležité alebo najzaujímavší detail.
- headline (max 75 znakov): "{count} herných správ týždňa" a 2 najväčšie témy.
- cta_title (max 45 znakov): výzva uložiť si to alebo poslať kamošovi, ktorý bol mimo.
- {caption_rules(cfg)}"""
    user = "Správy týždňa:\n" + json.dumps(listing, ensure_ascii=False)

    def write(feedback, previous):
        return ask_json(cfg["model"]["writer"], system, user + rewrite_note(feedback, previous) + "\nPriprav post.",
                        fmt=FMT, effort=_effort(cfg, "write"))

    def validate(c):
        items = c.get("items") or []
        idx = [it.get("index") for it in items]
        issues = []
        if len(items) != count or len(set(idx)) != count or any(not 0 <= i < len(week) for i in idx):
            issues.append(f"items: presne {count} rôznych správ zo zoznamu")
        for it in items:
            if len(it.get("label", "")) > 42 or len(it.get("title", "")) > 48 or len(it.get("body", "")) > 190:
                issues.append(f"položka {it.get('index')}: label max 36, title max 40, body max 160 znakov")
        if not c.get("cta_title") or len(c["cta_title"]) > 50:
            issues.append("cta_title chýba alebo má viac ako 45 znakov")
        return issues

    copy = produce_copy(write, cfg, validate=validate, facts=listing,
                        note="Zdroje sú vlastné overené posty stránky, porovnávaj s nimi.")
    start = today - timedelta(days=6)
    sub = f"{start.day}. – {today.day}. {_month(today)}" if start.month == today.month else \
        f"{start.day}. {_month(start)} – {today.day}. {_month(today)}"
    frames = [{"kind": "list_cover", "category": "TÝŽDEŇ V HRÁCH", "title_main": f"{count} správ, ktoré si",
               "title_hl": "mohol zmeškať", "sub": sub, "teaser": [{"label": it["label"]} for it in copy["items"]]}]
    for n, it in enumerate(copy["items"], start=1):
        p = week[it["index"]]
        d = datetime.fromisoformat(p["at"]).astimezone(TZ).date()
        frames.append({"kind": "list_item", "n": n, "title": it["title"], "body": it["body"], "chips": [short_date(d)],
                       "image_url": p.get("photo_url"), "photo_credit": p.get("photo_credit") if p.get("photo_url") else None})
    alts = [f"{count} herných správ týždňa ({sub}): " + "; ".join(it["label"] for it in copy["items"])] + \
           [f"{n}. {it['title']}. {it['body']}" for n, it in enumerate(copy["items"], start=1)]
    picked = [week[it["index"]] for it in copy["items"]]
    return build_post("digest", copy, frames, category="TÝŽDEŇ", story=f"Týždeň v správach {sub}",
                      key=f"digest:{today.isoformat()}", sources=sorted({s for p in picked for s in p.get("sources") or []}),
                      links=[], alts=alts, cta_type="save", verification="vlastné overené posty z týždňa")


def _month(d: date) -> str:
    return MONTHS[d.month - 1]
