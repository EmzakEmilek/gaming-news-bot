"""Redakcia: výber témy, overenie zdrojov, napísanie postu, čitateľská kontrola a kontrola faktov."""
from __future__ import annotations

import json
import re
from collections import Counter

from .collect import fetch_article
from .common import log, now_local
from .llm import ask_json

CATEGORIES = ["OZNÁMENIE", "TRAILER", "RELEASE", "UPDATE", "DLC", "BIZNIS", "HARDVÉR", "ESPORT", "ZDARMA", "DÁTUM VYDANIA"]
ARTICLE_CHARS = 3500  # koľko znakov z každého článku ide modelu (písanie aj fact-check)
CTA_ICONS = {"share": "send", "comment": "message-circle"}  # ikonka na záverečnej snímke (templates/icons)
DEFAULT_CTA = {"type": "share", "title": "Pošli to kamošovi, nech tiež vie"}
REPUTABLE = "Bloomberg, Reuters, The Verge, Kotaku, IGN, VGC, Eurogamer, GamesIndustry.biz, Game File"

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


def _today() -> str:
    """Dnešný dátum do promptov, aby model nepovažoval udalosti z tohto roka za budúcnosť."""
    d = now_local()
    return f"Dnes je {d.day}. {d.month}. {d.year}. Udalosti do tohto dňa sa už stali."


def _effort(cfg: dict, step: str) -> str | None:
    return cfg.get("effort", {}).get(step)


# ── 1. výber témy ────────────────────────────────────────────
def _choose_candidates(items: list[dict], recent: list[str], cfg: dict) -> list[dict]:
    listing = [
        {**{k: it[k] for k in ("id", "source", "tier", "title", "published")}, "summary": it["summary"][:200]}
        for it in items[:250]  # 25 feedov za 20 h dá v pracovný deň aj 200+ článkov
    ]
    system = f"""{_today()}
Si šéfredaktor slovenskej Instagram stránky o videohrách. Z dnešných článkov vyberáš
témy, ktoré zaujímajú bežného slovenského hráča (PC, PlayStation, Xbox, Nintendo, veľké mobilné hry).

Dobré témy: oznámenia nových hier, dátumy vydania, veľké trailery, významné updaty a DLC, hry zadarmo,
nový hardvér, veľké biznis správy (akvizície, zatvorenie štúdia), výsledky veľkých turnajov.
Slabé témy: recenzie, návody, zoznamy "top 10", názorové články, zľavové články, malé indie hry bez presahu.
Uprednostni správy, na ktoré ľudia reagujú alebo ich pošlú kamošovi: hry zadarmo a veľké zľavy známych hier,
veľké oznámenia, kontroverzné rozhodnutia firiem (prepúšťanie, zdražovanie, zrušené hry, zmeny, ktoré hráčov nahnevajú).

Nikdy nevyberaj tieto témy: {"; ".join(cfg["posting"]["avoid_topics"])}.

Viacero článkov o tej istej udalosti z rôznych portálov zlúč do jedného kandidáta. Prejdi celý zoznam
a do item_ids daj VŠETKY články o tej istej udalosti, aj z českých a slovenských webov a aj keď majú iný nadpis.
Nedávaj tam články o inej udalosti (napr. iná správa o tej istej hre).

is_rumor = true: anonymný leak, insider, príspevok zo sociálnych sietí, datamining alebo tvrdenie bez vlastného
zistenia konkrétneho média. is_report = true: správu vlastným zisťovaním priniesla konkrétna renomovaná redakcia
({REPUTABLE}), ale firma ju oficiálne nepotvrdila. Oficiálne oznámenia majú oboje false."""
    user = f"""Nedávno sme už postli (neopakuj tieto témy ani ich pokračovanie bez novej zásadnej informácie;
posledné 3 posty nech nie sú o tej istej sérii udalostí, napr. trikrát za sebou reorganizácia Xboxu):
{json.dumps(recent, ensure_ascii=False)}

Dnešné články:
{json.dumps(listing, ensure_ascii=False)}

Vráť najviac 8 najlepších kandidátov zoradených od najlepšieho:
{{"candidates": [{{
  "story": "jedna veta, o čom správa je (po slovensky)",
  "item_ids": ["id", "..."],
  "score": 1-10,
  "is_rumor": true/false,
  "is_report": true/false,
  "forbidden_topic": true/false,
  "already_covered": true/false
}}]}}"""
    data = ask_json(cfg["model"]["writer"], system, user, effort=_effort(cfg, "select"))
    return data.get("candidates", [])


def _find_more_sources(cand: dict, items: list[dict], by_id: dict, cfg: dict) -> None:
    """Kandidát s jediným zdrojom: nájde články s rovnakým zriedkavým slovom v nadpise (napr. názov hry)
    a nechá Claude potvrdiť, ktoré sú o tej istej udalosti. Nové ID pridá do cand["item_ids"]."""
    words = lambda t: {w.lower() for w in re.findall(r"[\wÀ-ž]{4,}", t)}  # noqa: E731
    df = Counter(w for it in items for w in words(it["title"]))
    own = [by_id[i] for i in cand.get("item_ids", []) if i in by_id]
    rare = {w for a in own for w in words(a["title"]) if df[w] <= 8}
    groups = {a.get("group") or a["source"] for a in own}
    pool = [it for it in items if it["id"] not in cand["item_ids"] and (it.get("group") or it["source"]) not in groups
            and words(it["title"]) & rare][:12]
    if not pool:
        return
    listing = [{"id": it["id"], "source": it["source"], "title": it["title"], "summary": it["summary"][:200]}
               for it in pool]
    user = f"""Správa: {cand["story"]}
Pôvodné články: {json.dumps([{"title": a["title"], "summary": a["summary"][:200]} for a in own], ensure_ascii=False)}

Ktoré z týchto článkov hovoria o TEJ ISTEJ udalosti (nie len o tej istej hre alebo firme)?
{json.dumps(listing, ensure_ascii=False)}

Vráť: {{"same": ["id", "..."]}}"""
    try:
        same = ask_json(cfg["model"]["writer"], "Porovnávaš herné správy.", user, effort="low").get("same", [])
    except Exception as e:  # noqa: BLE001 – doplnkový krok, neblokuje
        log.warning("Hľadanie ďalších zdrojov zlyhalo: %s", e)
        return
    added = [i for i in same if i in {it["id"] for it in pool}]
    if added:
        cand["item_ids"] = list(cand["item_ids"]) + added
        log.info("Doplnené zdroje k '%s': %s", cand["story"], [by_id[i]["source"] for i in added])


def _passes_verification(cand: dict, by_id: dict, cfg: dict) -> tuple[bool, str]:
    p = cfg["posting"]
    if cand.get("forbidden_topic"):
        return False, "zakázaná téma"
    if cand.get("already_covered"):
        return False, "už sme o tom postli"
    if cand.get("is_rumor") and not p.get("allow_rumors"):
        return False, "fáma/leak"
    if cand.get("is_report") and not p.get("allow_reputable_reports", True):
        return False, "nepotvrdená reportáž média"
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
def _write(story: str, articles: list[dict], cfg: dict, feedback: list[str] | None = None,
           previous: dict | None = None, last: bool = False) -> dict:
    p = cfg["posting"]
    src = [
        {"id": a["id"], "source": a["source"], "title": a["title"], "text": a["text"][:ARTICLE_CHARS]}
        for a in articles
    ]
    system = f"""{_today()}
Píšeš carousel posty pre slovenskú Instagram stránku o videohrách {cfg["brand"]["handle"]}.

TÓN:
{cfg["tone"]}

FAKTY (najdôležitejšie):
- Používaj IBA informácie, ktoré sú výslovne v dodaných článkoch. Nič nedopĺňaj z vlastnej pamäti.
- Dátumy, ceny, platformy, čísla a mená prepíš presne. Ak si nie si istý, radšej to vynechaj.
- Ak zdroje uvádzajú niečo ako neisté ("vraj", "podľa insiderov"), buď to vynechaj, alebo to jasne označ.
  Neistotu vyjadri raz a jednoducho ("mal by vyjsť v decembri").
- Ak správa stojí na zisteniach konkrétneho média a firma ju nepotvrdila, uveď to v nadpise alebo hneď
  v prvej snímke ("podľa Bloombergu ...") a nepodávaj ju ako hotový fakt.
- Súkromných ľudí (nie verejne známe osoby) neuvádzaj menom.
- Žiadne vlastné superlatívy ani hodnotenia firiem a ľudí. Kontroverziu ukáž cez fakty postavené vedľa seba,
  názor nechaj na čitateľov.

JAZYK:
- Prirodzená slovenčina, ako keď kamoš-hráč prerozpráva správu. Prekladaj význam, nie slová: anglické idiómy
  a firemné frázy neprekladaj doslovne ("great to see" nie je "je skvelé vidieť", ale "teší ma";
  "streamlining" podľa kontextu "škrty" alebo "zoštíhlenie firmy"). Citát prerozprávaj, ak by doslovný preklad
  znel neprirodzene.
- Čitateľovi tykaj v jednotnom čísle ("priprav si", "čo na to povieš?"), nikdy nie "vy".
- Názvy hier, firiem a produktov nechaj v origináli, všetko ostatné po slovensky. Ak by nesklonný cudzí názov
  znel vo vete zle, preformuluj vetu ("spadá pod štúdio Bethesda Game Studios", nie "zodpovedá sa Bethesda").
- Čísla po slovensky: 88 000 alebo 88 tisíc (nie 88-tisíc), 4,5 milióna, 15 %. Úvodzovky „takto“.
- Každá veta musí čitateľovi pridať niečo nové. Nepoužívaj vzorce, podľa ktorých ľudia spoznajú text od AI:
{AI_TELLS}
- Nikde nespomínaj AI, bota, automatizáciu ani to, ako post vznikol.

TITULNÁ SNÍMKA (je na nej len nadpis, nič iné):
- headline: max 75 znakov. Sám musí povedať, o akú hru alebo firmu ide a čo sa stalo (pri update napíš,
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
- body: max 220 znakov. Ak spomenieš pojem alebo človeka, ktorého nie každý pozná (Gamerscore, NG+, extraction,
  kreatívny riaditeľ), vysvetli ho pár slovami alebo ho vynechaj.

CAPTION A OSTATNÉ:
- caption: 2 až 4 krátke odseky, spolu max 900 znakov. Prvá veta je hook. Posledný odsek je jedna krátka výzva:
  poslať to kamošovi alebo napísať názor do komentu. Nepíš do captionu zdroje ani hashtagy, doplní ich systém.
- cta: návrh výzvy na poslednú snímku (finálnu podobu doladí editor): type "share" alebo "comment", title.
- hashtags: {p["max_hashtags"]} relevantných hashtagov: názov hry, platforma a aspoň 2 slovenské
  (napr. #hry #hernenovinky #gamingslovensko #novinkyzhier), zvyšok anglické.
- category: jedna z {CATEGORIES}.

PRED ODOVZDANÍM si post prečítaj ako človek, ktorý o téme nič nevie, a over:
- z nadpisu je jasné, o čo ide, a má hook; prvá snímka dáva kontext,
- nadpis, snímky a caption si neprotirečia a každý fakt je v zdrojoch,
- žiadny titulok snímky nie je vata, žiadna veta nie je z vyššie uvedených AI vzorcov."""
    sources = f"""Téma: {story}

Zdrojové články:
{json.dumps(src, ensure_ascii=False)}
"""
    user = ""
    if feedback and previous:  # opravuj predošlú verziu, nepíš odznova (inak vznikajú nové chyby)
        shown = {k: previous.get(k) for k in ("category", "headline", "slides", "cta", "caption", "hashtags")}
        user += ("\nTvoja predošlá verzia:\n" + json.dumps(shown, ensure_ascii=False)
                 + "\n\nOprav v nej LEN tieto problémy, všetko ostatné nechaj bez zmeny. Ak oprava žiada fakt,"
                 " ktorý v zdrojoch nie je, nedopĺňaj ho, radšej mätúcu časť preformuluj alebo vynechaj:\n- "
                 + "\n- ".join(feedback))
        if last:
            user += ("\n\nToto je posledný pokus. Ak sa problém nedá jednoducho opraviť, problematickú časť vynechaj."
                     " Post môže byť kratší, stačia 2 obsahové snímky.")
    user += """
Vráť:
{"category": "...", "headline": "...", "slides": [{"title": "...", "body": "..."}],
 "cta": {"type": "share"|"comment", "title": "..."}, "caption": "...", "hashtags": ["#..."],
 "facts_used": ["každý konkrétny fakt z postu + id článku, z ktorého pochádza"]}"""
    if feedback:  # opravy s väčšou hĺbkou uvažovania; môžu ísť dve za sebou, preto zdroje do cache
        post = ask_json(cfg["model"]["writer"], system, user, cached=sources, effort=_effort(cfg, "write_fix"))
    else:  # prvý pokus má inú hĺbku ako opravy (zmena effortu cache zneplatní), cache by sa len platila
        post = ask_json(cfg["model"]["writer"], system, sources + user, effort=_effort(cfg, "write"))
    post["format"] = "carousel"
    return post


# ── 3. čitateľská kontrola (bez zdrojov, lacná) ─────────────
def _review(post: dict, cfg: dict, previous: list[str] | None = None) -> dict:
    shown = {k: post.get(k) for k in ("headline", "slides", "caption")}
    system = f"""{_today()}
Si šéfredaktor slovenskej Instagram stránky o hrách. Čítaš hotový carousel tak, ako ho uvidí
bežný slovenský hráč, ktorý o téme doteraz nič nevedel. Zdroje nemáš, fakty kontroluje niekto iný.
Do "blocking" daj IBA tieto štyri druhy vážnych problémov:
1. Z nadpisu titulnej snímky nie je jasné, o akú hru alebo firmu ide a čo sa stalo.
2. Dve časti postu si protirečia (nadpis, snímky, caption).
3. Tvrdenie je nezrozumiteľné alebo mätúce, čitateľ nepochopí, čo znamená.
4. Zlá slovenčina (doslovný preklad, kalk, zlý pád), vykanie alebo zjavná AI fráza z týchto vzorcov
   (nesklonený cudzí názov firmy alebo hry je v poriadku, ak veta inak znie prirodzene):
{AI_TELLS}
Všetko ostatné daj do "minor": titulok snímky nesedí presne, chýba predstavenie mena alebo pojmu (ak text aj tak
dáva zmysel), slabší hook, formát čísel, iná formulácia, štýl. Výzvu na konci neposudzuj, doladí ju editor.
Nežiadaj doplnenie nových faktov (post smie obsahovať len to, čo je v zdrojoch). Ak chýba kontext,
navrhni preformulovať alebo vynechať časť, ktorá mätie. Každý problém napíš konkrétne aj s návrhom opravy."""
    user = f"""Post:
{json.dumps(shown, ensure_ascii=False)}
"""
    if previous:
        user += ("\nPredošlá verzia mala tieto problémy: " + json.dumps(previous, ensure_ascii=False)
                 + "\nOver, či sú opravené. Nový problém daj do blocking, len ak je naozaj vážny.\n")
    user += '\nVráť: {"blocking": ["..."], "minor": ["..."]}'
    return ask_json(cfg["model"]["checker"], system, user, effort=_effort(cfg, "review"))


# ── 4. kontrola faktov ───────────────────────────────────────
def _check(post: dict, articles: list[dict], cfg: dict) -> dict:
    src = [{"id": a["id"], "source": a["source"], "text": a["text"][:ARTICLE_CHARS]} for a in articles]
    shown = {k: post.get(k) for k in ("headline", "slides", "caption", "hashtags")}
    system = f"""{_today()}
Si prísny fact-checker. Porovnávaš hotový Instagram post so zdrojovými článkami. Kontroluješ len fakty,
slovenčinu a štýl kontroluje niekto iný.
Do "blocking" daj IBA:
- faktické tvrdenie (dátum, cena, platforma, číslo, meno, citát, udalosť), ktoré nie je v zdrojoch
  alebo je v rozpore so zdrojmi,
- zavádzajúci nadpis (jeho časť nie je pravdivá alebo vyvoláva nepravdivý dojem),
- fámu alebo nepotvrdenú správu podanú ako hotový fakt.
Nadpis smie byť úderný a postaviť fakty do kontrastu (napr. prepúšťanie vs. spokojný šéf), ak je každá jeho
časť pravdivá. Správa, ktorú post jasne pripisuje médiu ("podľa Bloombergu"), je v poriadku, ak ju zdroje uvádzajú.
Nepresnú formuláciu, ktorá nemení význam, daj do "minor"."""
    sources = f"""Zdroje:
{json.dumps(src, ensure_ascii=False)}
"""
    user = f"""Post:
{json.dumps(shown, ensure_ascii=False)}

Vráť: {{"blocking": ["konkrétny problém a ako ho opraviť"], "minor": ["..."]}}"""
    return ask_json(cfg["model"]["checker"], system, user, cached=sources, effort=_effort(cfg, "factcheck"))


# ── tvar a automatické opravy ────────────────────────────────
def _autofix(post: dict) -> None:
    """Drobnosti, ktoré netreba riešiť drahým prepisom."""
    def fix(t: str) -> str:
        t = re.sub(r"\s*—\s*|\s+–\s+", ", ", t)                                    # pomlčky medzi vetami
        t = re.sub(r"(\d)%", "\\1 %", t)                                            # 15% -> 15 %
        return re.sub(r"(\d)-(tisíc|milión\w*|miliard\w*)", "\\1 \\2", t)           # 88-tisíc -> 88 tisíc
    for key in ("headline", "caption"):
        if post.get(key):
            post[key] = fix(post[key])
    for sl in post.get("slides") or []:
        sl["title"], sl["body"] = fix(sl.get("title", "")), fix(sl.get("body", ""))
    if post.get("cta", {}).get("title"):
        post["cta"]["title"] = fix(post["cta"]["title"])
    caption = post.get("caption") or ""
    while len(caption) > 1100 and caption.count("\n\n") >= 2:  # dlhý caption: vypusti predposledný odsek
        parts = caption.split("\n\n")
        caption = "\n\n".join(parts[:-2] + parts[-1:])
    post["caption"] = caption


def _validate_shape(post: dict, cfg: dict) -> list[str]:
    _autofix(post)
    issues = []
    if not post.get("headline") or len(post["headline"]) > 90:
        issues.append("headline chýba alebo má viac ako 75 znakov")
    slides = post.get("slides") or []
    if not 2 <= len(slides) <= cfg["posting"]["carousel_max_slides"] - 2:  # + titulka a záverečná snímka
        issues.append("carousel musí mať 2 až %d snímky" % (cfg["posting"]["carousel_max_slides"] - 2))
    for sl in slides:  # šablóna text zmenší, limity sú s rezervou
        if len(sl.get("body", "")) > 300 or len(sl.get("title", "")) > 44:
            issues.append(f"snímka '{sl.get('title')}' je príliš dlhá")
    cta = post.get("cta") or {}
    if cta.get("type") not in CTA_ICONS or not cta.get("title") or len(cta["title"]) > 60:
        post["cta"] = dict(DEFAULT_CTA)  # výzvu aj tak doladí editor, kvôli nej sa neprepisuje
    if not post.get("caption") or len(post["caption"]) > 1200:
        issues.append("caption chýba alebo je dlhší ako 900 znakov")
    if post.get("category") not in CATEGORIES:
        post["category"] = "NOVINKA"
    return issues


def produce(story: str, articles: list[dict], cfg: dict) -> dict | None:
    """Napíše post a nechá ho prejsť kontrolou tvaru, čitateľa a faktov. Pri chybách max. 3 pokusy."""
    feedback, reader_issues, post = None, None, None
    for attempt in range(3):
        post = _write(story, articles, cfg, feedback, post, last=attempt == 2)
        stage, issues, minor = "tvar", _validate_shape(post, cfg), []
        if not issues:
            stage, verdict = "čitateľ", _review(post, cfg, reader_issues)
            issues, minor = verdict.get("blocking") or [], verdict.get("minor") or []
            reader_issues = issues or reader_issues
        if not issues:
            stage, verdict = "fakty", _check(post, articles, cfg)
            issues, minor = verdict.get("blocking") or [], minor + (verdict.get("minor") or [])
        if not issues:
            if minor:
                log.info("Drobnosti (nebránia publikovaniu): %s", minor)
            _polish_cta(post, cfg)
            return post
        log.info("Kontrola (%s) našla problémy (pokus %d): %s", stage, attempt + 1, issues)
        feedback = issues + minor
    return None


# ── 5. výzva na poslednej snímke (samostatne, s najvyššou hĺbkou uvažovania) ──
def _polish_cta(post: dict, cfg: dict) -> None:
    """Prepíše cta a posledný odsek captionu. Beží raz, až keď post prešiel kontrolami.
    Ak výsledok nesplní pravidlá, ostane pôvodná výzva od pisateľa."""
    shown = {k: post.get(k) for k in ("headline", "slides", "caption", "cta")}
    system = f"""Si copywriter slovenskej Instagram stránky o hrách {cfg["brand"]["handle"]}. Píšeš výzvu na poslednú
snímku carouselu a posledný odsek captionu. Cieľ je čo najviac zdieľaní a komentárov.
- type "share" (predvolené, zdieľanie je najsilnejšia interakcia): zaujímavá, užitočná alebo zábavná správa,
  ktorú človek pošle kamošovi. Napr. "Pošli zľavy kamošovi, nech tiež vie",
  "Pošli to parťákovi, s ktorým to budeš hrať".
- type "comment": kontroverzná alebo diskutabilná správa, kde ľudia budú mať názor. Napr.
  "Čo si myslíš? Daj vedieť do komentu", "Kúpiš si to za túto cenu? Napíš do komentu".
- title: max 45 znakov, konkrétne k tejto správe, úderné, prirodzená hovorová slovenčina, tykanie.
  Nevyzývaj na uloženie ani na sledovanie stránky. Žiadne anglické slová okrem názvov hier.
- caption_end: posledný odsek captionu, tá istá výzva inými slovami (max 150 znakov).
- Nepridávaj žiadne nové fakty, ktoré nie sú v poste. Žiadne pomlčky a žiadne typické AI frázy:
{AI_TELLS}"""
    user = f"""Post:
{json.dumps(shown, ensure_ascii=False)}

Vráť: {{"type": "share"|"comment", "title": "...", "caption_end": "..."}}"""
    try:
        new = ask_json(cfg["model"]["writer"], system, user, effort=_effort(cfg, "cta"))
    except Exception as e:  # noqa: BLE001 – výzva nie je kritická, ostane pôvodná
        log.warning("Výzvu sa nepodarilo vylepšiť: %s", e)
        return
    title, end = (new.get("title") or "").strip(), (new.get("caption_end") or "").strip()
    if new.get("type") not in CTA_ICONS or not title or len(title) > 60 or not end or len(end) > 200:
        log.warning("Vylepšená výzva nesplnila pravidlá, ostáva pôvodná: %s", new)
        return
    paragraphs = post["caption"].rstrip().split("\n\n")
    if len(paragraphs) > 1 and len(paragraphs[-1]) <= 250:  # posledný odsek je výzva od pisateľa
        paragraphs = paragraphs[:-1]
    post["caption"] = "\n\n".join(paragraphs + [end])
    post["cta"] = {"type": new["type"], "title": title}
    _autofix(post)
    log.info("Výzva: [%s] %s | %s", new["type"], title, end)


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
        if not ok and reason.startswith("len 1"):  # skús nájsť ďalšie weby, ktoré o tom písali
            _find_more_sources(cand, items, by_id, cfg)
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
