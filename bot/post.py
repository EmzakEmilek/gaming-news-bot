"""Hlavný beh: zober správy -> vyber a over -> napíš -> vyrenderuj -> publikuj.

Použitie:
  python -m bot.post              # ostrý beh
  python -m bot.post --dry-run    # všetko okrem publikovania (výstup v out/)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil

from .collect import collect
from .common import OUT_DIR, load_config, load_state, log, notify, now_local, save_state
from .editor import make_post
from .llm import report_cost, run_cost


def build_caption(post: dict, cfg: dict) -> str:
    tags = []
    for t in post.get("hashtags", []):
        t = "#" + re.sub(r"[^\w]", "", t.lstrip("#"))
        if len(t) > 1 and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    tags = tags[: min(cfg["posting"]["max_hashtags"], 30)]
    credit = f"Zdroj: {', '.join(post['sources'])}"
    if post.get("photo_credit"):
        credit += f" | Foto: {post['photo_credit']}"
    caption = f"{post['caption'].strip()}\n\n{credit}\n\n{' '.join(tags)}"
    return caption[:2200]


def slot_name(hour: int) -> str:
    return "rano" if hour < 15 else "vecer"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="postni aj keď už v tomto slote bol post")
    args = ap.parse_args()

    cfg = load_config()
    if not cfg.get("enabled", True):
        log.info("Bot je vypnutý v config.yaml (enabled: false).")
        return

    now = now_local()
    today, slot = now.date().isoformat(), slot_name(now.hour)
    posted: list[dict] = load_state("posted", [])
    if not args.dry_run and not args.force and any(p["date"] == today and p["slot"] == slot for p in posted):
        log.info("Slot %s %s už je postnutý, končím.", today, slot)
        return

    recent = [p["story"] for p in posted[-40:]]
    used_links = {link for p in posted[-300:] for link in p.get("links", [])}

    post = None
    for mult in (1, 2):
        items = collect(cfg["feeds"], cfg["posting"]["lookback_hours"] * mult, used_links)
        post = make_post(items, recent, cfg)
        if post:
            break
        log.info("Nič vhodné, rozširujem časové okno.")
    if not post:
        msg = f"⚠️ {cfg['brand']['name']}: slot {slot} {today} vynechaný – žiadna správa neprešla overením."
        log.warning(msg)
        notify(msg)
        return

    run_id = f"{today}-{slot}"
    if args.dry_run:  # každý testovací beh do vlastného priečinka
        run_id += now.strftime("-dry-%H%M%S")
    out_dir = OUT_DIR / run_id
    shutil.rmtree(out_dir, ignore_errors=True)  # žiadne staré snímky z predošlého behu
    from .render import render_post  # import až tu, Playwright je ťažký
    date_label = f"{now.day}. {now.month}. {now.year}"
    files = render_post(post, cfg, out_dir, date_label)
    caption = build_caption(post, cfg)
    post["cost_usd"] = round(run_cost(), 4)
    (out_dir / "post.json").write_text(json.dumps({**post, "final_caption": caption}, ensure_ascii=False, indent=2),
                                       encoding="utf-8")

    if args.dry_run:
        log.info("DRY RUN – nepublikujem. Výstup: %s\n\n%s", out_dir, caption)
        return

    from .hosting import upload
    from .instagram import Instagram

    ig = Instagram()
    urls = upload(files, run_id)
    info = ig.publish(urls, caption, ai_label=cfg["posting"].get("ai_label", False))

    posted.append({
        "date": today, "slot": slot, "story": post["story"], "headline": post["headline"],
        "links": post["links"], "sources": post["sources"], "format": post["format"],
        "media_id": info["id"], "permalink": info.get("permalink"), "cost_usd": post["cost_usd"],
    })
    save_state("posted", posted[-500:])
    notify(f"✅ {cfg['brand']['name']} postol: {post['headline']} (~${post['cost_usd']:.2f})\n{info.get('permalink', '')}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        notify(f"❌ Bot zlyhal pri postovaní: {e}")
        raise
    finally:
        report_cost("post")
