"""HTML šablóna -> JPEG 1080x1350 (4:5) cez Playwright."""
from __future__ import annotations

import io
from pathlib import Path

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image
from playwright.sync_api import sync_playwright

from .common import TEMPLATES_DIR, log

W, H = 1080, 1350


def _download_image(cands: list, dest: Path) -> tuple[str | None, str | None]:
    """Vráti (file URI, meno zdroja fotky) prvého použiteľného obrázka."""
    for c in cands:
        url, src = (c["url"], c.get("source")) if isinstance(c, dict) else (c, None)
        try:
            r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            if img.width < 800:
                continue
            img.save(dest, "JPEG", quality=92)
            return dest.resolve().as_uri(), src
        except Exception as e:  # noqa: BLE001
            log.warning("Obrázok %s sa nepodarilo použiť: %s", url, e)
    return None, None


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
        "kind": "cover", "image": image, "category": post["category"], "headline": post["headline"],
        "subline": post.get("subline"), "sources": ", ".join(post["sources"]), "photo_credit": photo_credit, "carousel": carousel,
        "ghost": post["category"].split()[0], "fit_max_height": 500 if image else 560,
    }]
    if carousel:
        body_slides = post["slides"]
        total = len(body_slides) + 2
        for i, s in enumerate(body_slides, start=2):
            slides_ctx.append({"kind": "text", "title": s["title"], "body": s["body"],
                               "index": i, "total": total, "fit_max_height": 300})
        slides_ctx.append({"kind": "outro", "index": total, "total": total, "fit_max_height": 0})

    files = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for n, ctx in enumerate(slides_ctx, start=1):
            html_path = out_dir / f"slide_{n}.html"
            html_path.write_text(tpl.render(brand=brand, base=base, date=date_label, **ctx), encoding="utf-8")
            page.goto(html_path.resolve().as_uri())
            page.wait_for_selector("body[data-ready='1']", timeout=15000)
            png = page.screenshot(clip={"x": 0, "y": 0, "width": W, "height": H})
            jpg = out_dir / f"slide_{n}.jpg"
            Image.open(io.BytesIO(png)).convert("RGB").save(jpg, "JPEG", quality=92, optimize=True)
            files.append(jpg)
        browser.close()
    log.info("Vyrenderovaných %d snímok do %s", len(files), out_dir)
    return files
