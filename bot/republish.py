"""Znova zverejní už publikovaný post z artefaktu jeho behu, bez volania Claude (napr. po oprave výzvy).
Starý post na Instagrame zmaže (ak to API dovolí, inak ho zmaže majiteľ a beh ide s --keep-old)
a v state/posted.json ho nahradí novým.

Použitie:
  python -m bot.republish <priečinok artefaktu> --cta "Nová otázka?"            # len ukáže, čo by zverejnil
  python -m bot.republish <priečinok artefaktu> --cta "Nová otázka?" --publish  # zmaže starý post a zverejní
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import OUT_DIR, load_config, load_state, log, notify, now_local, now_utc, save_state
from .post import alt_texts, build_caption


def load_post(folder: Path) -> tuple[dict, list[Path]]:
    """post.json a snímky ostrého behu (testovacie behy majú v názve priečinka "-dry-")."""
    found = [p for p in folder.rglob("post.json") if "-dry-" not in p.parent.name]
    if len(found) != 1:
        raise SystemExit(f"Čakal som 1 post.json ostrého behu, našiel som {len(found)}.")
    slides = sorted(found[0].parent.glob("slide_*.jpg"), key=lambda p: int(p.stem.split("_")[1]))
    return json.loads(found[0].read_text(encoding="utf-8")), slides


def replace_cta(post: dict, title: str) -> None:
    """Nová výzva na poslednej snímke aj ako posledný odsek captionu (tam bola pôvodná výzva)."""
    post["cta"] = {**(post.get("cta") or {"type": "comment"}), "title": title}
    paragraphs = post["caption"].rstrip().split("\n\n")
    post["caption"] = "\n\n".join(paragraphs[:-1] + [title])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--cta", help="nová výzva na poslednej snímke a na konci captionu")
    ap.add_argument("--publish", action="store_true", help="naozaj zmazať starý post a zverejniť")
    ap.add_argument("--keep-old", action="store_true", help="starý post nemazať (majiteľ ho zmazal alebo archivoval sám)")
    args = ap.parse_args()
    cfg = load_config()

    post, slides = load_post(args.folder)
    if args.cta:
        replace_cta(post, args.cta.strip())
        from .render import render_post  # import až tu, Playwright je ťažký
        credit = post.get("photo_credit")  # obrázky sa nesťahujú znova, mení sa len posledná snímka
        new = render_post({**post, "image_candidates": []}, cfg, OUT_DIR / "republish", "")
        post["photo_credit"] = credit
        slides = slides[:-1] + [new[-1]]
    caption = build_caption(post, cfg)
    log.info("Snímky: %s\nVýzva: %s\n\n%s", [p.name for p in slides], (post.get("cta") or {}).get("title"), caption)

    posted = load_state("posted", [])
    old = next((p for p in reversed(posted) if p.get("links") == post["links"]), None)
    if not old:
        raise SystemExit("Pôvodný post som v state/posted.json nenašiel.")
    if not args.publish:
        log.info("Len náhľad – nič nemažem ani nezverejňujem. Pôvodný post: %s", old.get("permalink"))
        return

    from .hosting import upload
    from .instagram import Instagram

    ig = Instagram()
    old_id = old["media_id"]
    if not args.keep_old:
        try:
            ig.delete_media(old_id)
        except Exception as e:  # Instagram Login API mazanie nepodporuje (len účty s Facebook Login)
            raise SystemExit(f"Starý post sa cez API zmazať nedá ({e}). Zmaž ho alebo archivuj v apke"
                             " a spusti znova s keep_old. Nič som nezverejnil.")
        log.info("Starý post zmazaný: %s", old.get("permalink"))
    urls = upload(slides, f"republish-{now_local():%Y%m%d-%H%M%S}")
    info = ig.publish(urls, caption, ai_label=cfg["posting"].get("ai_label", False), alt_texts=alt_texts(post))
    old.update({"at": now_utc().isoformat(), "media_id": info["id"], "permalink": info.get("permalink"), "story_id": None})
    save_state("posted", posted)
    insights = load_state("insights", {"media": {}, "followers": []})
    if insights.get("media", {}).pop(old_id, None):  # štatistiky zmazaného postu už neplatia
        save_state("insights", insights)
    notify(f"🔁 Post opravený a zverejnený znova: {post['headline']}\n{info.get('permalink', '')}"
           "\n📲 Zdieľaj do Story: otvor odkaz → ✈️ → Pridať do príbehu")


if __name__ == "__main__":
    main()
