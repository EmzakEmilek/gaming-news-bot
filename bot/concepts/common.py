"""Spoločné pre koncepty: jazyk, kontrola čitateľom a faktov, opakovanie pri chybách, výsledný post."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from .. import photos as ph
from ..common import log, now_utc
from ..editor import (AI_TELLS, CAPTION_HOOK, REVIEW_FMT, UNTRUSTED, VERDICT_FMT, _apply_fixes, _autofix, _effort,
                      _today)
from ..llm import ask_json, run_cost

WEEKDAYS = ["PO", "UT", "ST", "ŠT", "PI", "SO", "NE"]
MONTHS_SHORT = ["jan", "feb", "mar", "apr", "máj", "jún", "júl", "aug", "sep", "okt", "nov", "dec"]


class ConceptSkip(Exception):
    """Koncept dnes nevyšiel (málo dát, nič nové, neprešiel kontrolou) – slot dostane bežnú správu."""


def language(cfg: dict) -> str:
    return f"""TÓN
{cfg["tone"]}
JAZYK
- Prirodzená hovorová slovenčina, tykaj v jednotnom čísle. Prekladaj význam, nie slová, žiadne české tvary.
- Názvy hier, firiem a produktov nechaj v origináli. Čísla po slovensky (88 000, 15 %), úvodzovky „takto“.
- Čas vždy konkrétne (dátum slovom: "1. októbra"), nikdy "zajtra" ani "tento týždeň".
- Nepíš tvary hral(a) ani hral/a, použi formuláciu bez rodu.
- Nepoužívaj vzorce typické pre AI:
{AI_TELLS}
- Nespomínaj AI ani to, ako post vznikol."""


def caption_rules(cfg: dict) -> str:
    return (f"caption: 2 až 4 krátke odseky (max 900 znakov). Prvá veta je hook, najviac {CAPTION_HOOK} znakov"
            " (Instagram vo feede ukáže len začiatok). Posledný odsek je krátka výzva. Bez hashtagov a zdrojov.\n"
            f"hashtags: 3 až {cfg['posting']['max_hashtags']} cielených (hra alebo séria, platforma, 1 až 2 slovenské"
            " ako #hry). Žiadne všeobecné (#gaming, #fyp, #viral).")


def caption_issues(copy: dict, max_len: int = 1200) -> list[str]:
    cap = (copy.get("caption") or "").strip()
    if not cap or len(cap) > max_len:
        return ["caption chýba alebo je dlhší ako 900 znakov"]
    first = re.split(r"(?<=[.!?])\s|\n", cap, maxsplit=1)[0]
    if len(first) > CAPTION_HOOK:
        return [f"prvá veta captionu má {len(first)} znakov, skráť ju na {CAPTION_HOOK}"]
    return []


def check_budget(cfg: dict) -> None:
    cap = cfg.get("concepts", {}).get("max_cost", 0.35)
    if run_cost() >= cap:
        raise ConceptSkip(f"koncept minul ${run_cost():.2f} (limit ${cap:.2f})")


def recent_posts(posted: list[dict], days: int, concept: str | None = None) -> list[dict]:
    """Posty za posledných `days` dní (voliteľne len jedného konceptu; staršie posty bez konceptu sú správy)."""
    cutoff = now_utc() - timedelta(days=days)
    return [p for p in posted if p.get("at") and datetime.fromisoformat(p["at"]) >= cutoff
            and (concept is None or p.get("concept", "news") == concept)]


def used_keys(posted: list[dict], concept: str, days: int) -> set[str]:
    return {p["key"] for p in recent_posts(posted, days, concept) if p.get("key")}


def first_image(urls: list[str | None]) -> str | None:
    """Prvá adresa, z ktorej sa dá stiahnuť dosť veľký obrázok (Steam niektoré formáty pri starších hrách nemá)."""
    for u in urls:
        if not u:
            continue
        img = ph.fetch(u)
        if img is not None and img.width >= 600:
            return u
    return None


def review(copy: dict, cfg: dict, rules: str, note: str = "") -> list[str]:
    """Čitateľská kontrola (bez zdrojov): drobné opravy urobí kód, vráti problémy na prepis."""
    shown = {k: v for k, v in copy.items() if k not in ("hashtags", "key") and not k.startswith("_") and v}
    system = f"""{_today()}
Si šéfredaktor slovenskej Instagram stránky o hrách. Čítaš hotový carousel ako bežný slovenský hráč.
"fixes" – chyby, ktoré opraví prepísanie pár slov: gramatika, česká alebo doslovne preložená väzba, vykanie,
  relatívny čas, zopakovaná informácia, vzorec z tohto zoznamu:
{AI_TELLS}
  find = presný úsek z textu skopírovaný doslova, replace = opravené znenie (prázdne = vypustiť), bez nového faktu.
"blocking" – len vážne veci: nezrozumiteľné alebo nelogické tvrdenie, rozpor medzi časťami, urážka, a tiež:
{rules}
"minor" – štýl, ktorý žiadne pravidlo neporušuje.
{note}"""
    verdict = ask_json(cfg["model"]["checker"], system, "Post:\n" + json.dumps(shown, ensure_ascii=False),
                       fmt=REVIEW_FMT, effort=_effort(cfg, "review"))
    missed = _apply_fixes(copy, verdict.get("fixes") or [])
    if missed:  # jazykové drobnosti, ktoré kód nevedel nahradiť: do prepisu áno, kvôli nim koncept nepadne
        log.info("Koncept: opravy, ktoré sa nenašli v texte: %s", missed)
        copy.setdefault("_nits", []).extend(missed)
    return verdict.get("blocking") or []


def factcheck(copy: dict, facts, cfg: dict, note: str = "") -> list[str]:
    """Porovná texty postu s dátami alebo článkami, z ktorých vznikol."""
    shown = {k: v for k, v in copy.items() if k != "key" and not k.startswith("_") and v}
    system = f"""{_today()}
Si prísny fact-checker. Porovnávaš Instagram post so zdrojmi (dáta a články nižšie). {UNTRUSTED}
"blocking": tvrdenie (dátum, cena, číslo, meno, udalosť), ktoré v zdrojoch nie je alebo im odporuje, formulácia,
ktorá posúva význam, nadpis, ktorý tvrdí viac ako zdroje. Označený názor stránky nie je fakt, posudzuj len fakty v ňom.
"minor": nepresnosť, ktorá nemení význam.
{note}"""
    sources = "Zdroje:\n" + json.dumps(facts, ensure_ascii=False, default=str)
    verdict = ask_json(cfg["model"]["checker"], system, "Post:\n" + json.dumps(shown, ensure_ascii=False),
                       fmt=VERDICT_FMT, cached=sources, effort=_effort(cfg, "factcheck"))
    return verdict.get("blocking") or []


def produce_copy(write, cfg: dict, validate=None, rules: str = "", facts=None, note: str = "",
                 attempts: int = 3, review_note: str = "") -> dict:
    """write(feedback, previous) -> texty. Kontrola tvaru (kód), čitateľ a fakty; pri chybách prepis.
    Jazykové opravy, ktoré kód nevedel nahradiť, idú do prepisu len spolu so skutočným problémom."""
    feedback, copy = None, None
    for attempt in range(attempts):
        check_budget(cfg)
        copy = write(feedback, copy)
        _autofix(copy)
        stage, issues = "tvar", (validate(copy) if validate else []) + caption_issues(copy)
        if not issues:
            stage, issues = "čitateľ", review(copy, cfg, rules, review_note)
        if not issues and facts is not None:
            stage, issues = "fakty", factcheck(copy, facts, cfg, note)
        nits = copy.pop("_nits", [])
        if not issues:
            return copy
        log.info("Koncept: kontrola (%s) našla problémy (pokus %d): %s", stage, attempt + 1, issues)
        feedback = issues + nits
    raise ConceptSkip(f"text neprešiel kontrolami: {feedback}")


def rewrite_note(feedback: list[str] | None, previous: dict | None) -> str:
    """Pokyn na opravu predošlej verzie (nepísať odznova, inak vznikajú nové chyby)."""
    if not feedback or not previous:
        return ""
    return ("\n\nTvoja predošlá verzia:\n" + json.dumps(previous, ensure_ascii=False)
            + "\nOprav v nej LEN tieto problémy, ostatné nechaj:\n- " + "\n- ".join(feedback))


def short_date(d) -> str:
    """UT 21. okt"""
    return f"{WEEKDAYS[d.weekday()]} {d.day}. {MONTHS_SHORT[d.month - 1]}"


def build_post(concept: str, copy: dict, frames: list[dict], *, category: str, story: str, key: str,
               sources: list[str], links: list[str], alts: list[str], cta_type: str,
               photo_credit: str | None = None, verification: str = "") -> dict:
    return {
        "concept": concept, "format": "carousel", "category": category, "headline": copy["headline"],
        "hook": copy.get("hook"), "caption": copy["caption"], "hashtags": copy.get("hashtags") or [],
        "cta": {"type": cta_type, "title": copy["cta_title"]}, "frames": frames, "alts": alts, "story": story,
        "key": key, "sources": sources, "links": links, "verification": verification, "rumor": False,
        "photos": [], "photo_credit": photo_credit,
    }
