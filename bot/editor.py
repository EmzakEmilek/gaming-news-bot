"""Redakcia: výber témy, overenie zdrojov, napísanie postu, čitateľská kontrola a kontrola faktov."""
from __future__ import annotations

import json
import re

from .collect import fetch_article
from .common import log
from .llm import ask_json

CATEGORIES = ["OZNÁMENIE", "TRAILER", "RELEASE", "UPDATE", "DLC", "BIZNIS", "HARDVÉR", "ESPORT", "ZDARMA", "DÁTUM VYDANIA"]
CTA_ICONS = {"share": "send", "comment": "message-circle"}  # ikonka na záverečnej snímke (templates/icons)

# Vzorce, podľa ktorých ľudia spoznajú text od AI (podľa skillu humanizer / Wikipedia "Signs of AI writing").
AI_TELLS = """- kontrast "nie je to len X, ale Y", "nejde o X, ide o Y", "X, nie Y" (povedz rovno, čo platí)
- dramatické jednovetné závery a fragmenty ("A to nie je všetko.", "Presne tak.", "Zmena je tu.")
- úvody, ktoré ohlasujú namiesto toho, aby povedali ("Poďme sa pozrieť", "Tu je, čo vieme", "Úprimne?")
- vymenúvanie po troch len pre rytmus
- pomlčky (– alebo —) ako spojka viet; použi čiarku, bodku alebo dvojbodku
- nafúknutý význam ("míľnik", "zásadný moment", "píše históriu", "mení pravidlá hry", "budúcnosť vyzerá svetlo")
- reklamné slová ("úchvatný", "ohromujúci", "nabitý novinkami", "bohatý obsah")
- "slúži ako", "predstavuje" namiesto obyčajného "je"; "podľa dostupných informácií"
- vata a všeobecné titulky ("Ešte jedna novinka", "Čo ďalej", "Detaily", "Zhrnutie", "Zaujímavosť")"""


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
Uprednostni správy, na ktoré ľudia reagujú alebo ich pošlú kamošovi: hry zadarmo a veľké zľavy známych hier,
veľké oznámenia, kontroverzné rozhodnutia firiem (prepúšťanie, zdražovanie, zrušené hry, zmeny, ktoré hráčov nahnevajú).

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
    data = ask_json(cfg["model"]["writer"], system, user)
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
    system = f"""Píšeš carousel posty pre slovenskú Instagram stránku o videohrách {cfg["brand"]["handle"]}.

TÓN:
{cfg["tone"]}

FAKTY (najdôležitejšie):
- Používaj IBA informácie, ktoré sú výslovne v dodaných článkoch. Nič nedopĺňaj z vlastnej pamäti.
- Dátumy, ceny, platformy, čísla a mená prepíš presne. Ak si nie si istý, radšej to vynechaj.
- Ak zdroje uvádzajú niečo ako neisté ("vraj", "podľa insiderov"), buď to vynechaj, alebo to jasne označ.
  Neistotu vyjadri raz a jednoducho ("mal by vyjsť v decembri").
- Súkromných ľudí (nie verejne známe osoby) neuvádzaj menom.
- Žiadne vlastné superlatívy ani hodnotenia firiem a ľudí. Kontroverziu ukáž cez fakty postavené vedľa seba,
  názor nechaj na čitateľov.

JAZYK:
- Prirodzená slovenčina, ako keď kamoš-hráč prerozpráva správu. Prekladaj význam, nie slová: anglické idiómy
  a firemné frázy neprekladaj doslovne ("great to see" nie je "je skvelé vidieť", ale "teší ma";
  "streamlining" podľa kontextu "škrty" alebo "zoštíhlenie firmy"). Citát prerozprávaj, ak by doslovný preklad
  znel neprirodzene.
- Čitateľovi tykaj v jednotnom čísle ("priprav si", "čo na to povieš?"), nikdy nie "vy".
- Názvy hier, firiem a produktov nechaj v origináli, všetko ostatné po slovensky.
- Čísla po slovensky: 88 000 alebo 88 tisíc (nie 88-tisíc), 4,5 milióna, 15 %. Úvodzovky „takto“.
- Každá veta musí čitateľovi pridať niečo nové. Nepoužívaj vzorce, podľa ktorých ľudia spoznajú text od AI:
{AI_TELLS}
- Nikde nespomínaj AI, bota, automatizáciu ani to, ako post vznikol.

TITULNÁ SNÍMKA (je na nej len nadpis, nič iné):
- headline: max 70 znakov. Sám musí povedať, o akú hru alebo firmu ide a čo sa stalo (pri update napíš,
  že ide o update; pri menej známej hre krátko, čo to je). Zároveň musí mať hook, aby človek swipol:
  - kontroverzná správa (prepúšťanie, škrty, súdy, zdražovanie, zrušené hry): vyhroť kontrast, ktorý je
    vo faktoch, napr. "Xbox vo veľkom prepúšťa, šéf Microsoftu je spokojný",
  - dobrá správa (zadarmo, zľavy, nová hra): konkrétny prínos alebo číslo, napr. "Prvá Castlevania je zadarmo
    a séria má zľavy až 80 %",
  - informácia: najzaujímavejší konkrétny detail.
  Hook musí byť pravdivý a podložený zdrojmi, žiadny clickbait. Nekonči bodkou.

OBSAHOVÉ SNÍMKY:
- slides: 2 až {p["carousel_max_slides"] - 2} snímky. Prvá snímka dá kontext pre niekoho, kto o téme nič nevie:
  čo je to za hru alebo vec a čo presne sa stalo. Ďalšie pridávajú detaily. Nič neopakuj.
- title: max 32 znakov, konkrétne zhrnie obsah snímky ("Ľadové jaskyne v decembri", "Zadarmo do 24. októbra").
- body: max 220 znakov. Ak spomenieš pojem, ktorý nie každý pozná (Gamerscore, NG+, extraction), vysvetli ho
  pár slovami alebo ho vynechaj.

POSLEDNÁ SNÍMKA (cta, je na nej len jeden nadpis):
- type "share" (predvolené, zdieľanie je najsilnejšia interakcia): zaujímavá, užitočná alebo zábavná správa,
  ktorú človek pošle kamošovi. Napr. "Pošli zľavy kamošovi, nech tiež vie",
  "Pošli to parťákovi, s ktorým to budeš hrať".
- type "comment": kontroverzná alebo diskutabilná správa. Napr. "Čo si myslíš? Daj vedieť do komentu",
  "Kúpiš si to za túto cenu? Napíš do komentu".
- title: max 45 znakov, napojený na obsah postu. Nevyzývaj na uloženie ani na sledovanie stránky.

CAPTION A OSTATNÉ:
- caption: 2 až 4 krátke odseky, spolu max 900 znakov. Prvá veta je hook. Posledná veta je tá istá výzva
  ako cta. Nepíš do captionu zdroje ani hashtagy, doplní ich systém.
- hashtags: {p["max_hashtags"]} relevantných hashtagov: názov hry, platforma a aspoň 2 slovenské
  (napr. #hry #hernenovinky #gamingslovensko #novinkyzhier), zvyšok anglické.
- category: jedna z {CATEGORIES}."""
    user = f"""Téma: {story}

Zdrojové články:
{json.dumps(src, ensure_ascii=False)}
"""
    if feedback:
        user += ("\nPredchádzajúca verzia mala tieto chyby, oprav ich. Ak oprava žiada fakt, ktorý v zdrojoch nie je,"
                 " nedopĺňaj ho, radšej mätúcu časť preformuluj alebo vynechaj:\n- " + "\n- ".join(feedback))
    user += """
Vráť:
{"category": "...", "headline": "...", "slides": [{"title": "...", "body": "..."}],
 "cta": {"type": "share"|"comment", "title": "..."}, "caption": "...", "hashtags": ["#..."],
 "facts_used": ["každý konkrétny fakt z postu + id článku, z ktorého pochádza"]}"""
    post = ask_json(cfg["model"]["writer"], system, user)
    post["format"] = "carousel"
    return post


# ── 3. čitateľská kontrola (bez zdrojov, lacná) ─────────────
def _review(post: dict, cfg: dict, previous: list[str] | None = None) -> dict:
    shown = {k: post.get(k) for k in ("headline", "slides", "cta", "caption")}
    system = f"""Si šéfredaktor slovenskej Instagram stránky o hrách. Čítaš hotový carousel tak, ako ho uvidí
bežný slovenský hráč, ktorý o téme doteraz nič nevedel. Zdroje nemáš, fakty kontroluje niekto iný.
Rozhoduješ, či post môže ísť von. Do "blocking" daj len vážne problémy, kvôli ktorým by nemal ísť von:
1. Z nadpisu titulnej snímky nie je jasné, o akú hru alebo firmu ide a čo sa stalo, alebo nadpis nemá hook.
2. Čitateľ nepochopí, o čo ide: kľúčový pojem bez vysvetlenia, neznáma hra bez predstavenia, tvrdenie,
   ktoré nadväzuje na niečo, čo post nepovedal, alebo si dve časti postu protirečia.
3. Prvá obsahová snímka nedáva kontext, titulok snímky nesedí s jej obsahom alebo je to vata.
4. Zlá slovenčina: doslovný preklad z angličtiny, kalk, zlý pád, vykanie.
5. Zjavné AI frázy. Vzorce:
{AI_TELLS}
6. Výzva na poslednej snímke nesedí s obsahom alebo caption nekončí rovnakou výzvou.
Všetko ostatné (formát čísel, voliteľné doplnenie detailu, iná formulácia, štýlová preferencia) daj do "minor".
Nežiadaj doplnenie nových faktov (post smie obsahovať len to, čo je v zdrojoch). Ak chýba kontext,
navrhni preformulovať alebo vynechať časť, ktorá mätie.
Každý problém napíš konkrétne aj s návrhom opravy."""
    user = f"""Post:
{json.dumps(shown, ensure_ascii=False)}
"""
    if previous:
        user += ("\nPredošlá verzia mala tieto problémy: " + json.dumps(previous, ensure_ascii=False)
                 + "\nOver, či sú opravené. Nový problém daj do blocking, len ak je naozaj vážny.\n")
    user += '\nVráť: {"blocking": ["..."], "minor": ["..."]}'
    return ask_json(cfg["model"]["checker"], system, user)


# ── 4. kontrola faktov ──────────────────────────────────────────────
def _check(post: dict, articles: list[dict], cfg: dict) -> dict:
    src = [{"id": a["id"], "source": a["source"], "text": a["text"][:5000]} for a in articles]
    shown = {k: post.get(k) for k in ("headline", "slides", "cta", "caption", "hashtags")}
    system = """Si prísny fact-checker. Porovnávaš hotový Instagram post so zdrojovými článkami.
Post schváľ iba vtedy, ak KAŽDÉ faktické tvrdenie (dátum, cena, platforma, číslo, meno, citát, udalosť)
je podložené zdrojmi. Kontroluj aj: zavádzajúci headline, fámu podanú ako fakt, zlú slovenčinu
(gramatika, diakritika, anglické frázy doslovne preložené), urážlivý alebo necitlivý obsah.
Headline smie byť úderný a postaviť fakty do kontrastu (napr. prepúšťanie vs. spokojný šéf), ak je každá
jeho časť pravdivá a podložená. Výzva na poslednej snímke (cta) nie je faktické tvrdenie."""
    user = f"""Zdroje:
{json.dumps(src, ensure_ascii=False)}

Post:
{json.dumps(shown, ensure_ascii=False)}

Vráť: {{"ok": true/false, "issues": ["konkrétny problém a ako ho opraviť"]}}"""
    return ask_json(cfg["model"]["checker"], system, user)


def _autofix(post: dict) -> None:
    """Drobnosti, ktoré netreba riešiť drahým prepisom: pomlčky medzi vetami, medzera pred %."""
    def fix(t: str) -> str:
        t = re.sub(r"\s*—\s*|\s+–\s+", ", ", t)
        return re.sub(r"(\d)%", "\\1 %", t)
    for key in ("headline", "caption"):
        if post.get(key):
            post[key] = fix(post[key])
    for sl in post.get("slides") or []:
        sl["title"], sl["body"] = fix(sl.get("title", "")), fix(sl.get("body", ""))
    if post.get("cta", {}).get("title"):
        post["cta"]["title"] = fix(post["cta"]["title"])


def _validate_shape(post: dict, cfg: dict) -> list[str]:
    _autofix(post)
    issues = []
    if not post.get("headline") or len(post["headline"]) > 85:
        issues.append("headline chýba alebo má viac ako 70 znakov")
    slides = post.get("slides") or []
    if not 2 <= len(slides) <= cfg["posting"]["carousel_max_slides"] - 2:  # + titulka a záverečná snímka
        issues.append("carousel musí mať 2 až %d snímky" % (cfg["posting"]["carousel_max_slides"] - 2))
    for sl in slides:  # šablóna text zmenší, limity sú s rezervou
        if len(sl.get("body", "")) > 280 or len(sl.get("title", "")) > 44:
            issues.append(f"snímka '{sl.get('title')}' je príliš dlhá")
    cta = post.get("cta") or {}
    if cta.get("type") not in CTA_ICONS:
        issues.append('cta.type musí byť "share" alebo "comment"')
    if not cta.get("title") or len(cta["title"]) > 60:
        issues.append("cta chýba alebo má viac ako 45 znakov")
    if not post.get("caption") or len(post["caption"]) > 1200:
        issues.append("caption chýba alebo je dlhší ako 900 znakov")
    if post.get("category") not in CATEGORIES:
        post["category"] = "NOVINKA"
    return issues


def produce(story: str, articles: list[dict], cfg: dict) -> dict | None:
    """Napíše post a nechá ho prejsť kontrolou tvaru, čitateľa a faktov. Pri chybách max. 3 pokusy."""
    feedback, reader_issues = None, None
    for attempt in range(3):
        post = _write(story, articles, cfg, feedback)
        stage, issues = "tvar", _validate_shape(post, cfg)
        minor = []
        if not issues:
            stage, verdict = "čitateľ", _review(post, cfg, reader_issues)
            issues, minor = verdict.get("blocking") or [], verdict.get("minor") or []
            reader_issues = issues or reader_issues
        if not issues:
            stage, verdict = "fakty", _check(post, articles, cfg)
            issues = [] if verdict.get("ok") else (verdict.get("issues") or ["fact-check neschválil"])
        if not issues:
            if minor:
                log.info("Drobnosti (nebránia publikovaniu): %s", minor)
            return post
        log.info("Kontrola (%s) našla problémy (pokus %d): %s", stage, attempt + 1, issues)
        feedback = issues + minor
    return None


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
        post = produce(cand["story"], articles, cfg)
        if post:
            return finalize(post, cand["story"], articles, reason, cfg)
        log.info("Téma '%s' neprešla kontrolou, skúšam ďalšiu.", cand["story"])
    return None


def finalize(post: dict, story: str, articles: list[dict], reason: str, cfg: dict) -> dict:
    post["sources"] = sorted({a["source"] for a in articles})
    post["links"] = [a["link"] for a in articles]
    post["story"] = story
    post["verification"] = reason
    post["image_candidates"] = _image_candidates(articles, cfg)
    return post


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
