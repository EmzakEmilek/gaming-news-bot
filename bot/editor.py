"""Redakcia: výber témy, overenie zdrojov, napísanie postu a nezávislá kontrola faktov."""
from __future__ import annotations

import json

from .collect import fetch_article
from .common import log
from .llm import ask_json

CATEGORIES = ["OZNÁMENIE", "TRAILER", "RELEASE", "UPDATE", "DLC", "BIZNIS", "HARDVÉR", "ESPORT", "ZDARMA", "DÁTUM VYDANIA"]


# ── 1. výber témy ────────────────────────────────────────────
def _choose_candidates(items: list[dict], recent: list[str], cfg: dict) -> list[dict]:
    listing = [
        {k: it[k] for k in ("id", "source", "tier", "title", "summary", "published")}
        for it in items[:250]  # 25 feedov za 20 h dá v pracovný deň aj 200+ článkov
    ]
    system = f"""Si šéfredaktor slovenskej Instagram stránky o videohrách. Z dnešných článkov vyberáš
témy, ktoré zaujímajú bežného slovenského hráča (PC, PlayStation, Xbox, Nintendo, veľké mobilné hry).

Dobré témy: oznámenia nových hier, dátumy vydania, veľké trailery, významné updaty a DLC, hry zadarmo,
nový hardvér, veľké biznis správy (akvizície, zatvorenie štúdia), výsledky veľkých turnajov.
Slabé témy: recenzie, návody, zoznamy "top 10", názorové články, zľavové články, malé indie hry bez presahu.

Nikdy nevyberaj tieto témy: {"; ".join(cfg["posting"]["avoid_topics"])}.

Viacero článkov o tej istej udalosti z rôznych portálov zlúč do jedného kandidáta (item_ids).
Do item_ids daj IBA články, ktoré naozaj hovoria o tej istej udalosti."""
    user = f"""Nedávno sme už postli (neopakuj tieto témy ani ich pokračovanie bez novej zásadnej informácie):
{json.dumps(recent, ensure_ascii=False)}

Dnešné články:
{json.dumps(listing, ensure_ascii=False)}

Vráť najviac 6 najlepších kandidátov zoradených od najlepšieho:
{{"candidates": [{{
  "story": "jedna veta, o čom správa je (po slovensky)",
  "item_ids": ["id", "..."],
  "score": 1-10,
  "is_rumor": true/false,   // leak, fáma, insider, "reportedly" bez oficiálneho potvrdenia
  "forbidden_topic": true/false,
  "already_covered": true/false
}}]}}"""
    data = ask_json(cfg["model"]["writer"], system, user, max_tokens=3000)
    return data.get("candidates", [])


def _passes_verification(cand: dict, by_id: dict, cfg: dict) -> tuple[bool, str]:
    p = cfg["posting"]
    if cand.get("forbidden_topic"):
        return False, "zakázaná téma"
    if cand.get("already_covered"):
        return False, "už sme o tom postli"
    if cand.get("is_rumor") and not p.get("allow_rumors"):
        return False, "fáma/leak"
    arts = [by_id[i] for i in cand.get("item_ids", []) if i in by_id]
    if not arts:
        return False, "neplatné ID článkov"
    sources = {a["source"] for a in arts}
    publishers = {a.get("group") or a["source"] for a in arts}  # weby jedného vydavateľa = 1 zdroj
    official = any(a["tier"] == "official" for a in arts)
    if official and p.get("official_is_enough", True):
        return True, f"oficiálny zdroj ({', '.join(sorted(sources))})"
    if len(publishers) >= p.get("min_trusted_sources", 2):
        return True, f"{len(publishers)} nezávislé zdroje ({', '.join(sorted(sources))})"
    return False, f"len {len(publishers)} nezávislý zdroj ({', '.join(sorted(sources))})"


# ── 2. písanie ───────────────────────────────────────────────
def _write(story: str, articles: list[dict], cfg: dict, feedback: list[str] | None = None) -> dict:
    p = cfg["posting"]
    src = [
        {"id": a["id"], "source": a["source"], "title": a["title"], "text": a["text"][:5000]}
        for a in articles
    ]
    system = f"""Píšeš posty pre slovenskú Instagram stránku o videohrách {cfg["brand"]["handle"]}.

TÓN:
{cfg["tone"]}

PRAVIDLÁ FAKTOV (najdôležitejšie):
- Používaj IBA informácie, ktoré sú výslovne v dodaných článkoch. Nič nedopĺňaj z vlastnej pamäti.
- Dátumy, ceny, platformy, čísla a mená prepíš presne. Ak si nie si istý, radšej to vynechaj.
- Ak zdroje uvádzajú niečo ako neisté ("vraj", "podľa insiderov"), buď to vynechaj, alebo to jasne označ.
- Názvy hier, firiem a produktov nechaj v origináli (neprekladaj).

FORMÁT:
- "single" pre jednoduchú správu (jedna hlavná informácia), "carousel" keď je viac podstatných detailov.
- headline: max 60 znakov, úderný, vecný, bez clickbaitu. Nekonči bodkou.
- subline: max 110 znakov, doplní headline o najdôležitejší detail.
- slides (iba carousel): 2 až {p["carousel_max_slides"] - 1} snímky, každá title max 32 znakov a body max 220 znakov.
- caption: 2 až 4 krátke odseky, spolu max 900 znakov. Prvá veta je hook. Na konci jedna otázka pre komentáre.
  Nepíš do captionu zdroje ani hashtagy, doplní ich systém.
- hashtags: {p["max_hashtags"]} relevantných hashtagov (mix slovenských a anglických, názov hry, platforma).
- category: jedna z {CATEGORIES}."""
    user = f"""Téma: {story}

Zdrojové články:
{json.dumps(src, ensure_ascii=False)}
"""
    if feedback:
        user += "\nPredchádzajúca verzia mala tieto chyby, oprav ich:\n- " + "\n- ".join(feedback)
    user += """
Vráť:
{"format": "single"|"carousel", "category": "...", "headline": "...", "subline": "...",
 "slides": [{"title": "...", "body": "..."}], "caption": "...", "hashtags": ["#..."],
 "facts_used": ["každý konkrétny fakt z postu + id článku, z ktorého pochádza"]}"""
    return ask_json(cfg["model"]["writer"], system, user, max_tokens=3000)


# ── 3. kontrola ──────────────────────────────────────────────
def _check(post: dict, articles: list[dict], cfg: dict) -> dict:
    src = [{"id": a["id"], "source": a["source"], "text": a["text"][:5000]} for a in articles]
    shown = {k: post.get(k) for k in ("headline", "subline", "slides", "caption", "hashtags")}
    system = """Si prísny fact-checker. Porovnávaš hotový Instagram post so zdrojovými článkami.
Post schváľ iba vtedy, ak KAŽDÉ faktické tvrdenie (dátum, cena, platforma, číslo, meno, citát, udalosť)
je podložené zdrojmi. Kontroluj aj: zavádzajúci headline, fámu podanú ako fakt, zlú slovenčinu
(gramatika, diakritika, anglické frázy doslovne preložené), urážlivý alebo necitlivý obsah."""
    user = f"""Zdroje:
{json.dumps(src, ensure_ascii=False)}

Post:
{json.dumps(shown, ensure_ascii=False)}

Vráť: {{"ok": true/false, "issues": ["konkrétny problém a ako ho opraviť"]}}"""
    return ask_json(cfg["model"]["checker"], system, user, max_tokens=1500)


def _validate_shape(post: dict, cfg: dict) -> list[str]:
    issues = []
    if post.get("format") not in ("single", "carousel"):
        issues.append("format musí byť single alebo carousel")
    if not post.get("headline") or len(post["headline"]) > 70:
        issues.append("headline chýba alebo má viac ako 60 znakov")
    if len(post.get("subline") or "") > 130:
        issues.append("subline má viac ako 110 znakov")
    if post.get("format") == "carousel":
        slides = post.get("slides") or []
        if not 2 <= len(slides) <= cfg["posting"]["carousel_max_slides"] - 1:
            issues.append("carousel musí mať 2 až %d snímky" % (cfg["posting"]["carousel_max_slides"] - 1))
        for s in slides:
            if len(s.get("body", "")) > 260 or len(s.get("title", "")) > 40:
                issues.append(f"snímka '{s.get('title')}' je príliš dlhá")
    if not post.get("caption") or len(post["caption"]) > 1200:
        issues.append("caption chýba alebo je dlhší ako 900 znakov")
    if post.get("category") not in CATEGORIES:
        post["category"] = "NOVINKA"
    return issues


def make_post(items: list[dict], recent: list[str], cfg: dict) -> dict | None:
    """Vráti hotový, overený post alebo None, ak dnes nie je nič dosť dobré a overené."""
    if not items:
        log.warning("Žiadne čerstvé články.")
        return None
    by_id = {it["id"]: it for it in items}
    candidates = _choose_candidates(items, recent, cfg)
    log.info("Kandidáti: %s", [c.get("story") for c in candidates])

    tried = 0
    for cand in candidates:
        ok, reason = _passes_verification(cand, by_id, cfg)
        log.info("[%s] %s -> %s", "OK" if ok else "SKIP", cand.get("story"), reason)
        if not ok:
            continue
        tried += 1
        if tried > 3:
            break
        articles = [fetch_article(by_id[i]) for i in cand["item_ids"] if i in by_id][:5]
        feedback = None
        for attempt in range(2):
            post = _write(cand["story"], articles, cfg, feedback)
            issues = _validate_shape(post, cfg)
            if not issues:
                verdict = _check(post, articles, cfg)
                issues = [] if verdict.get("ok") else (verdict.get("issues") or ["checker neschválil"])
            if not issues:
                post["sources"] = sorted({a["source"] for a in articles})
                post["links"] = [a["link"] for a in articles]
                post["story"] = cand["story"]
                post["verification"] = reason
                post["image_candidates"] = _image_candidates(articles, cfg)
                return post
            log.info("Kontrola našla problémy (pokus %d): %s", attempt + 1, issues)
            feedback = issues
        log.info("Téma '%s' neprešla kontrolou, skúšam ďalšiu.", cand["story"])
    return None


def _image_candidates(articles: list[dict], cfg: dict) -> list[dict]:
    """Obrázky z článkov: oficiálne zdroje prvé, podľa posting.article_images."""
    policy = cfg["posting"].get("article_images", "all")
    if policy == "none":
        return []
    out = []
    for a in sorted(articles, key=lambda a: a.get("tier") != "official"):
        if not a.get("image"):
            continue
        if policy == "official" and a.get("tier") != "official":
            continue
        out.append({"url": a["image"], "source": a["source"]})
    return out
