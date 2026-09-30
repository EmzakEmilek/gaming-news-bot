"""HTML šablóna -> JPEG 1080x1350 (4:5) cez Playwright."""
from __future__ import annotations

import io
import random
import re
from pathlib import Path

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageEnhance, ImageFilter
from playwright.sync_api import sync_playwright

from .common import TEMPLATES_DIR, log
from .editor import CTA_ICONS

W, H = 1080, 1350


def _typo(text: str | None) -> str | None:
    """Slovenská typografia: jednopísmenové predložky a spojky ani jednotky za číslom nenechávať na konci riadku."""
    if not text:
        return text
    text = re.sub(r"(?<![^\s(])([aAiIkKoOsSuUvVzZ]) ", "\\1\u00a0", text)
    text = re.sub(r"(?<!\d)(\d{1,2}\.) (?=\w)", "\\1\u00a0", text)  # radová číslovka: "1. októbra", "3. séria"
    return re.sub(r"(\d) (?=[^\s\d]{1,4}(?:[\s.,!?)]|$))", "\\1\u00a0", text)


def _icon(name: str | None) -> Markup:
    """Inline SVG ikona zo sady Lucide (templates/icons), farbu preberá z CSS. Používa sa len na záverečnej snímke."""
    path = TEMPLATES_DIR / "icons" / f"{name}.svg"
    if not name or not path.exists():
        return Markup("")
    svg = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S).strip()
    return Markup(svg)


def _split_cta(title: str) -> tuple[str, str]:
    """Výzvu rozdelí na bielu časť a farebne zvýraznenú: za prvou vetou/čiarkou, inak posledné 2 slová."""
    m = re.match(r"(.+?[?!,:.])\s+(.+)", title)
    if m:
        return m.group(1), m.group(2)
    words = title.split(" ")  # len bežné medzery: slová spojené nezlomiteľnou medzerou (predložka + slovo) ostanú spolu
    return " ".join(words[:-2]), " ".join(words[-2:])


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def _flow(slides: int, brand: dict, seed: str) -> str:
    """Jeden gradient cez celý carousel (šírka = počet snímok x 1080 px). Každá snímka ukazuje svoj výsek,
    takže farebné škvrny na hranách pokračujú na ďalšej snímke a každá snímka vyzerá trochu inak."""
    rnd = random.Random(seed)
    colors = [brand["accent"], brand["accent_2"], brand.get("accent_3", brand["accent_2"])]
    layers = []
    for k in range(slides + 1):  # škvrna na každom prechode medzi snímkami (a na krajoch)
        c = colors[(k + rnd.randint(0, 2)) % 3]
        layers.append(f"radial-gradient({rnd.randint(560, 760)}px {rnd.randint(480, 680)}px at "
                      f"{k * W + rnd.randint(-90, 90)}px {rnd.randint(250, H - 150)}px, "
                      f"{_rgba(c, 0.36 if c == colors[0] else 0.58)}, transparent 70%)")
    for k in range(slides):  # menšia škvrna pri hornom alebo dolnom okraji snímky
        c = colors[rnd.randint(0, 2)]
        y = rnd.choice([rnd.randint(-120, 120), rnd.randint(H - 120, H + 120)])
        layers.append(f"radial-gradient(460px 380px at {k * W + rnd.randint(260, 820)}px {y}px, "
                      f"{_rgba(c, 0.4)}, transparent 70%)")
    return ", ".join(layers)


def _fetch_image(url: str) -> Image.Image | None:
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:  # noqa: BLE001
        log.warning("Obrázok %s sa nepodarilo použiť: %s", url, e)
        return None


def _download_image(cands: list, dest: Path) -> tuple[str | None, str | None]:
    """Stiahne všetky kandidátske obrázky naraz a vyberie najlepší: aspoň 800 px na šírku,
    prednosť má oficiálny zdroj, potom najväčšie rozlíšenie. Vráti (file URI, meno zdroja fotky)."""
    cands = [c if isinstance(c, dict) else {"url": c} for c in cands][:6]
    with ThreadPoolExecutor(max_workers=6) as pool:
        images = list(pool.map(lambda c: _fetch_image(c["url"]), cands))
    usable = [(c, img) for c, img in zip(cands, images) if img and img.width >= 800]
    if not usable:
        return None, None
    c, img = max(usable, key=lambda ci: (bool(ci[0].get("official")), ci[1].width * ci[1].height))
    img.save(dest, "JPEG", quality=92)
    log.info("Fotka: %s (%dx%d)", c.get("source"), img.width, img.height)
    return dest.resolve().as_uri(), c.get("source")


def render_story(cover: Path, dest: Path) -> Path:
    """Story 1080x1920 z titulky postu: titulka v strede na rozmazanom a stmavenom pozadí z nej samej."""
    img = Image.open(cover).convert("RGB")
    scale = max(1080 / img.width, 1920 / img.height)
    bg = img.resize((round(img.width * scale), round(img.height * scale)))
    left, top = (bg.width - 1080) // 2, (bg.height - 1920) // 2
    bg = bg.crop((left, top, left + 1080, top + 1920)).filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.45)
    front = img.resize((1080, round(img.height * 1080 / img.width)))
    bg.paste(front, (0, (1920 - front.height) // 2))
    bg.save(dest, "JPEG", quality=90)
    return dest


def render_post(post: dict, cfg: dict, out_dir: Path, date_label: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]))
    tpl = env.get_template("slide.html")
    brand = cfg["brand"]
    base = TEMPLATES_DIR.resolve().as_uri() + "/"

    image, photo_credit = _download_image(post.get("image_candidates", []), out_dir / "source.jpg")
    post["photo_credit"] = photo_credit
    carousel = post["format"] == "carousel"
    slides_ctx = [{
        "kind": "cover", "image": image, "category": post["category"], "headline": _typo(post["headline"]),
        "photo_credit": photo_credit, "carousel": carousel, "ghost": post["category"].split()[0],
        "fit_max_height": 620 if image else 720,
    }]
    if carousel:
        body_slides = post["slides"]
        total = len(body_slides) + 2
        for i, s in enumerate(body_slides, start=2):
            slides_ctx.append({"kind": "text", "title": _typo(s["title"]), "body": _typo(s["body"]),
                               "index": i, "total": total, "fit_max_height": 300})
        cta = post.get("cta") or {}
        cta_main, cta_hl = _split_cta(_typo(cta.get("title") or "Pošli to kamošovi"))
        slides_ctx.append({"kind": "outro", "index": total, "total": total, "fit_max_height": 0,
                           "cta_main": cta_main, "cta_hl": cta_hl,
                           "cta_icon": _icon(CTA_ICONS.get(cta.get("type"), "send"))})
    flow = _flow(len(slides_ctx), brand, post["headline"])

    files = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for n, ctx in enumerate(slides_ctx, start=1):
            html_path = out_dir / f"slide_{n}.html"
            html = tpl.render(brand=brand, base=base, date=date_label, flow=flow, flow_w=len(slides_ctx) * W,
                              flow_x=(n - 1) * W, **ctx)
            html_path.write_text(html, encoding="utf-8")
            page.goto(html_path.resolve().as_uri())
            page.wait_for_selector("body[data-ready='1']", timeout=15000)
            png = page.screenshot(clip={"x": 0, "y": 0, "width": W, "height": H})
            jpg = out_dir / f"slide_{n}.jpg"
            Image.open(io.BytesIO(png)).convert("RGB").save(jpg, "JPEG", quality=88, optimize=True)  # IG aj tak rekomprimuje
            files.append(jpg)
        browser.close()
    log.info("Vyrenderovaných %d snímok do %s", len(files), out_dir)
    return files
