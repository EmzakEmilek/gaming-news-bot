"""Hlavný beh: zober správy -> vyber a over -> napíš -> vyrenderuj -> publikuj.

Použitie:
  python -m bot.post              # ostrý beh
  python -m bot.post --dry-run    # všetko okrem publikovania (výstup v out/)
  python -m bot.post --copy-only --count 2   # len texty (bez grafiky a publikovania), 2 rôzne témy
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil

from datetime import datetime, timedelta

from .collect import collect
from .common import OUT_DIR, load_config, load_state, log, notify, now_local, now_utc, save_state
from .editor import BudgetExceeded, make_post
from .insights import performance_hint
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


def _in_slot_window(now, cfg: dict) -> bool:
    """Záložný plánovač GitHubu beží v UTC a vie meškať aj hodiny – postovať smie len chvíľu PO čase slotu.
    Pred slotom nie, inak by v zimnom čase (cron o hodinu skôr) predbehol cron-job.org."""
    window = timedelta(minutes=cfg["posting"].get("schedule_window_minutes", 75))
    for hhmm in cfg["posting"].get("slot_times", []):
        h, m = map(int, hhmm.split(":"))
        if timedelta(0) <= now - now.replace(hour=h, minute=m, second=0, microsecond=0) <= window:
            return True
    return False


def copy_text(post: dict, cfg: dict) -> str:
    """Celý text postu na kontrolu (titulka, snímky, výzva, caption)."""
    lines = [f"[{post['category']}] {post['headline']}", ""]
    lines += [f"{n}. {sl['title']}\n   {sl['body']}" for n, sl in enumerate(post["slides"], start=2)]
    lines += [f"Výzva ({post['cta']['type']}): {post['cta']['title']}", "", "CAPTION:", build_caption(post, cfg),
              "", f"Overenie: {post['verification']}", f"Odkazy: {', '.join(post['links'])}"]
    return "\n".join(lines)


def copy_only(count: int, recent: list[str], used_links: set[str], cfg: dict, rumors_left: int) -> None:
    """Testovací režim: napíše `count` postov na rôzne témy, bez grafiky a publikovania, texty vypíše do logu."""
    items = collect(cfg["feeds"], cfg["posting"]["lookback_hours"], used_links)
    performance = performance_hint()
    OUT_DIR.mkdir(exist_ok=True)
    spent = 0.0
    for n in range(1, count + 1):
        try:
            post = make_post([it for it in items if it["link"] not in used_links], recent, cfg, [], performance,
                             rumors_left > 0)
        except BudgetExceeded as e:
            log.warning("Test zastavený, prekročený rozpočet behu: %s", e)
            return
        if not post:
            log.warning("Testovací post %d: žiadna téma neprešla kontrolami.", n)
            return
        cost, spent = run_cost() - spent, run_cost()  # cena tohto postu vrátane výberu témy a neúspešných pokusov
        log.info("\n===== TESTOVACÍ POST %d (cena ~$%.3f) =====\n%s\n", n, cost, copy_text(post, cfg))
        (OUT_DIR / f"copy-{n}.json").write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
        recent.append(post["story"])
        used_links |= set(post["links"])
        rumors_left -= post.get("rumor", False)


def alt_texts(post: dict) -> list[str]:
    """Alt text ku každej snímke = text, ktorý je na nej (pre nevidiacich a vyhľadávanie na Instagrame)."""
    cover = f"{post['category']}: {post['headline']}"
    if post.get("photo_credit"):
        cover += f". Fotka: {post['photo_credit']}"
    alts = [cover] + [f"{sl['title']}. {sl['body']}" for sl in post.get("slides") or []]
    if post.get("cta"):
        alts.append(post["cta"]["title"])
    return [a[:1000] for a in alts]


def github_output(key: str, value: str) -> None:
    """Hodnota pre ďalší krok workflowu (napr. či sa publikovalo – spustí rýchle komentáre)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def slot_name(hour: int) -> str:
    return "rano" if hour < 15 else "vecer"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="postni aj keď už v tomto slote bol post")
    ap.add_argument("--copy-only", action="store_true", help="len texty, bez grafiky a publikovania")
    ap.add_argument("--count", type=int, default=1, help="počet testovacích postov pri --copy-only")
    args = ap.parse_args()
    if args.copy_only:
        args.dry_run = True

    cfg = load_config()
    if not cfg.get("enabled", True):
        log.info("Bot je vypnutý v config.yaml (enabled: false).")
        return

    now = now_local()
    today, slot = now.date().isoformat(), slot_name(now.hour)
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and not args.force and not _in_slot_window(now, cfg):
        log.info("Plánovaný beh GitHubu mimo okna slotu (oneskorený), nič nerobím.")
        return
    posted: list[dict] = load_state("posted", [])
    if not args.dry_run and not args.force and any(p["date"] == today and p["slot"] == slot for p in posted):
        log.info("Slot %s %s už je postnutý, končím.", today, slot)
        return

    # témy, ktoré v posledných 48 h neprešli kontrolami – nevyberať ich znova (už sme za ne zaplatili)
    failed_state = [f for f in load_state("failed", [])
                    if now_utc() - datetime.fromisoformat(f["at"]) < timedelta(hours=48)]
    gap = cfg["posting"].get("min_hours_between_posts", 0)
    last_at = max((datetime.fromisoformat(p["at"]) for p in posted if p.get("at")), default=None)
    if not args.dry_run and not args.force and gap and last_at and now_utc() - last_at < timedelta(hours=gap):
        log.info("Posledný post bol pred menej ako %s h, tento slot vynechávam.", gap)
        return

    recent = [p["story"] for p in posted[-40:]] + [f["story"] for f in failed_state]
    used_links = {link for p in posted[-300:] for link in p.get("links", [])}
    used_links |= {link for f in failed_state for link in f["links"]}

    # fámy (štítok RUMOR) najviac posting.rumors_per_day denne, nech profil nestratí dôveryhodnosť
    rumors_left = cfg["posting"].get("rumors_per_day", 1) - sum(1 for p in posted if p["date"] == today and p.get("rumor"))

    if args.copy_only:
        copy_only(max(1, args.count), recent, used_links, cfg, rumors_left)
        return

    post, failed, reason = None, [], "žiadna správa neprešla overením"
    performance = performance_hint()
    try:
        for mult in (1, 2):
            items = collect(cfg["feeds"], cfg["posting"]["lookback_hours"] * mult, used_links)
            post = make_post(items, recent, cfg, failed, performance, rumors_left > 0)
            if post:
                break
            recent += [f["story"] for f in failed]  # v širšom okne už neskúšať to, čo práve neprešlo
            used_links |= {link for f in failed for link in f["links"]}
            log.info("Nič vhodné, rozširujem časové okno.")
    except BudgetExceeded as e:
        reason = f"prekročený rozpočet behu: {e}"
    if not args.dry_run and failed:
        stamp = now_utc().isoformat()
        save_state("failed", failed_state + [{**f, "at": stamp} for f in failed])
    if not post:
        msg = f"⚠️ {cfg['brand']['name']}: slot {slot} {today} vynechaný – {reason}."
        log.warning(msg)
        notify(msg)
        if not args.dry_run:  # do týždenného prehľadu
            skipped = load_state("skipped", []) + [{"date": today, "slot": slot, "at": now_utc().isoformat(),
                                                    "reason": reason}]
            save_state("skipped", skipped[-100:])
        return

    run_id = f"{today}-{slot}"
    if args.dry_run:  # každý testovací beh do vlastného priečinka
        run_id += now.strftime("-dry-%H%M%S")
    out_dir = OUT_DIR / run_id
    shutil.rmtree(out_dir, ignore_errors=True)  # žiadne staré snímky z predošlého behu
    from .render import render_post, render_story  # import až tu, Playwright je ťažký
    date_label = f"{now.day}. {now.month}. {now.year}"
    files = render_post(post, cfg, out_dir, date_label)
    story = render_story(files[0], out_dir / "story.jpg") if cfg["posting"].get("story", True) else None
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
    # nová URL pri každom behu, inak by CDN Pages mohla vrátiť staré snímky
    urls = upload(files + ([story] if story else []), f"{run_id}-{now:%H%M%S}")
    story_url = urls.pop() if story else None
    info = ig.publish(urls, caption, ai_label=cfg["posting"].get("ai_label", False), alt_texts=alt_texts(post))
    github_output("published", "true")
    story_id = None
    if story_url:  # post už je vonku – zlyhanie Story ho nesmie zhodiť
        try:
            story_id = ig.publish_story(story_url)
        except Exception as e:  # noqa: BLE001
            log.warning("Story sa nepodarilo zverejniť: %s", e)
            notify(f"⚠️ Post je vonku, ale Story sa nepodarilo zverejniť: {e}")

    # stav hneď po publikovaní: ak by neskôr niečo zlyhalo, ďalší slot tú istú správu nezopakuje
    posted.append({
        "date": today, "slot": slot, "at": now_utc().isoformat(), "story": post["story"], "headline": post["headline"],
        "links": post["links"], "sources": post["sources"], "format": post["format"], "category": post["category"], "rumor": post.get("rumor", False),
        "media_id": info["id"], "permalink": info.get("permalink"), "cost_usd": post["cost_usd"], "story_id": story_id,
    })
    save_state("posted", posted[-500:])
    notify(f"✅ {cfg['brand']['name']} postol: {post['headline']} (~${post['cost_usd']:.2f})"
           f"{' + Story' if story_id else ''}\n{info.get('permalink', '')}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        notify(f"❌ Bot zlyhal pri postovaní: {e}")
        raise
    finally:
        report_cost("post")
