"""Fotky k postu: hlavné obrázky článkov a obrázky z ich textu. Stiahne ich, nechá len dosť veľké fotky
na šírku, odstráni duplikáty (ten istý tlačový obrázok na viacerých weboch) a zoradí ich.
Prvá fotka ide na titulku, ďalšie môže pisateľ priradiť k snímkam.

Test bez Claude a bez publikovania (fotky k posledným postom + ukážka snímok):
  python -m bot.photos test --last 5
"""
from __future__ import annotations

import io
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

import requests
from PIL import Image

from .common import load_state, log, save_state

MIN_WIDTH = 800
ASPECT = (1.2, 2.4)       # len fotky na šírku: screenshoty 16:9 a tlačové fotky, nie logá, bannery ani portréty
MAX_CANDIDATES = 24       # koľko obrázkov najviac sťahovať na jeden post
MAX_PER_ARTICLE = 6
MAX_PHOTOS = 6            # titulka + najviac 4 obsahové snímky, jedna navyše pre výber
SIMILAR = 10              # rozdiel odtlačkov (z 64 bitov), pod ktorým ide o ten istý obrázok
SKIP_URL = re.compile(r"logo|avatar|author|icon|sprite|banner|placeholder|emoji|badge|\.svg|\.gif"
                      r"|sutaz|soutez|contest|giveaway|promo|advert|sponsor|partner|reklam", re.I)
SEEN_KEEP = 400           # koľko adries obrázkov z textu si pamätať (reklamy a bannery webu sa opakujú pri rôznych správach)

_cache: dict[str, Image.Image | None] = {}  # v jednom behu sa fotka sťahuje len raz (výber aj vykreslenie)


def body_images(page: str, url: str) -> list[dict]:
    """Obrázky z hlavného textu článku (trafilatura vynechá menu, reklamy a súvisiace články)."""
    import trafilatura
    xml = trafilatura.extract(page, include_images=True, output_format="xml", url=url)
    if not xml:
        return []
    out = []
    for g in ElementTree.fromstring(xml).iter("graphic"):
        src = g.get("src") or ""
        if src.startswith("http") and not SKIP_URL.search(src):
            out.append({"url": src, "alt": (g.get("alt") or g.get("title") or "").strip()[:160]})
    return out[:MAX_PER_ARTICLE]


def candidates(articles: list[dict], policy: str = "all", body: bool = True) -> list[dict]:
    """Kandidáti z článkov podľa posting.article_images: oficiálne zdroje prvé, hlavný obrázok pred obrázkami z textu.
    body=False: len hlavné obrázky (fotka len na titulku)."""
    if policy == "none":
        return []
    out, seen = [], set()
    for a in sorted(articles, key=lambda a: a.get("tier") != "official"):
        official = a.get("tier") == "official"
        if policy == "official" and not official:
            continue
        imgs = ([{"url": a["image"], "alt": "", "main": True}] if a.get("image") else []) + \
            (list(a.get("images") or []) if body else [])
        for im in imgs:
            if im["url"] not in seen:
                seen.add(im["url"])
                out.append({**im, "source": a["source"], "official": official})
    return out[:MAX_CANDIDATES]


def fetch(url: str) -> Image.Image | None:
    if url not in _cache:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            _cache[url] = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            log.warning("Obrázok %s sa nepodarilo použiť: %s", url, e)
            _cache[url] = None
    return _cache[url]


def _fingerprint(img: Image.Image) -> int:
    """Rozdielový odtlačok (dHash): rovnaký obrázok v inom rozlíšení alebo kompresii má takmer rovnaký odtlačok."""
    px = img.convert("L").resize((9, 8), Image.LANCZOS).tobytes()
    return sum(1 << i for i in range(64) if px[i // 8 * 9 + i % 8] > px[i // 8 * 9 + i % 8 + 1])


def label(photo: dict) -> str:
    """Čo o fotke vie pisateľ: popis z webu, inak slová z názvu súboru."""
    if photo.get("alt"):
        return photo["alt"]
    name = unquote(Path(urlparse(photo["url"]).path).stem)
    words = [w for w in re.split(r"[-_+.\s]+", name) if w.isalpha() and len(w) > 1]
    return " ".join(words) or "bez popisu"


def gather(cands: list[dict]) -> list[dict]:
    """Stiahne kandidátov naraz, nechá použiteľné fotky bez duplikátov. Prvá je najlepšia na titulku
    (oficiálny zdroj, hlavný obrázok článku, potom najväčšia), ostatné v poradí, v akom sú v článkoch."""
    seen = load_state("photo_seen", [])
    body = [c["url"] for c in cands if not c.get("main")]
    cands = [c for c in cands if c.get("main") or c["url"] not in seen]  # obrázok z textu už bol pri inej správe
    save_state("photo_seen", (seen + [u for u in body if u not in seen])[-SEEN_KEEP:])
    with ThreadPoolExecutor(max_workers=8) as pool:
        images = list(pool.map(lambda c: fetch(c["url"]), cands))
    usable = []
    for c, img in zip(cands, images):
        if not img or img.width < MIN_WIDTH or not ASPECT[0] <= img.width / img.height <= ASPECT[1]:
            continue
        usable.append({**c, "w": img.width, "h": img.height, "_fp": _fingerprint(img)})
    if not usable:
        return []
    best = max(usable, key=lambda c: (c["official"], bool(c.get("main")), c["w"] * c["h"]))
    unique = []
    for c in [best] + [c for c in usable if c is not best]:
        same = next((u for u in unique if bin(u["_fp"] ^ c["_fp"]).count("1") <= SIMILAR), None)
        if same is None:
            unique.append(c)
        elif c["w"] * c["h"] > same["w"] * same["h"] and same is not unique[0]:  # z duplikátov ostane väčší
            unique[unique.index(same)] = c
    photos = [{k: v for k, v in c.items() if k != "_fp"} for c in unique[:MAX_PHOTOS]]
    log.info("Fotky: %d z %d kandidátov (%s)", len(photos), len(cands), ", ".join(p["source"] for p in photos))
    return photos


def save(photo: dict, dest: Path) -> str | None:
    """Uloží fotku pre vykreslenie. Vráti file URI alebo None (pri opakovanom behu sa môže stiahnuť znova)."""
    img = fetch(photo["url"])
    if not img:
        return None
    img.save(dest, "JPEG", quality=92)
    return dest.resolve().as_uri()


# ── test bez Claude ──────────────────────────────────────────
def _test(last: int) -> None:
    import json
    import shutil

    from .collect import fetch_article
    from .common import OUT_DIR, load_config, load_state
    from .render import render_post

    cfg = load_config()
    names = {f["name"]: f.get("tier") for f in cfg["feeds"]}
    root = OUT_DIR / "photo-test"
    shutil.rmtree(root, ignore_errors=True)
    summary = []
    for n, p in enumerate(load_state("posted", [])[-last:], start=1):
        articles = []
        for link in p["links"]:  # zdroj podľa domény (posted.json má len zoznam mien zdrojov)
            host = urlparse(link).netloc.lower()
            source = next((s for s in p["sources"] if s.lower().replace(" ", "").split(".")[0] in host), host)
            a = fetch_article({"link": link, "title": "", "source": source, "summary": ""})
            a["tier"] = names.get(source, "trusted")
            articles.append(a)
        cands = candidates(articles, cfg["posting"].get("article_images", "all"))
        photos = gather(cands)
        # ukážka: text z krátkeho popisu správy, fotky v poradí (v ostrom behu ich priradí pisateľ podľa popisu)
        sentences = [s.strip() for s in re.split(r"(?<=[.:])\s+", p["story"]) if s.strip()]
        slides = [{"title": f"Ukážka snímky {i}", "body": (sentences[(i - 1) % len(sentences)])[:160],
                   "photo": i + 1 if i + 1 <= len(photos) else 0} for i in range(1, 4)]
        post = {"format": "carousel", "category": p.get("category") or "NOVINKA", "headline": p["headline"],
                "slides": slides, "cta": {"type": "comment", "title": "Čo na to hovoríš?"}, "photos": photos}
        out = root / f"{n}"
        files = render_post(post, cfg, out, "30. 9. 2026")
        sheet = Image.new("RGB", (4 * 540 + 30, 675), "white")
        for i, f in enumerate(files[:4]):
            sheet.paste(Image.open(f).resize((540, 675)), (i * 550, 0))
        sheet.save(root / f"post{n}.jpg", "JPEG", quality=80)
        summary.append({"post": p["headline"], "kandidati": len(cands), "fotky": len(photos),
                        "zoznam": [f'{ph["source"]} {ph["w"]}x{ph["h"]}: {label(ph)}' for ph in photos]})
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["test"])
    ap.add_argument("--last", type=int, default=5)
    _test(ap.parse_args().last)
