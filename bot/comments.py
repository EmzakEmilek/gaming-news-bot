"""Automatické odpovede na komentáre a skrývanie toxických komentárov.

Použitie:
  python -m bot.comments            # ostrý beh
  python -m bot.comments --dry-run  # len vypíše, čo by urobil
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta

from .common import load_config, load_state, log, notify, now_utc, save_state
from .instagram import Instagram
from .llm import ask_json

URL_RE = re.compile(r"(https?://|www\.|\.com\b|\.sk\b|\.cz\b)", re.I)


def _policy(cfg: dict) -> str:
    return f"""Spravuješ komentáre na slovenskej Instagram stránke o videohrách {cfg["brand"]["handle"]}.
Pre každý komentár rozhodni jednu akciu:

"reply" – odpovedz, ak:
  - je to otázka k správe (odpovedaj IBA z textu postu; ak odpoveď v poste nie je, povedz úprimne,
    že to zatiaľ nie je známe / oficiálne potvrdené),
  - je to názor alebo reakcia na hru (krátko a prirodzene zareaguj, pokojne sa opýtaj späť),
  - je to pochvala stránky (poďakuj, bez podlézania).
"hide" – skry, ak ide o urážky, nenávisť, rasizmus, vyhrážky, sexuálny obsah, spam, podvodné odkazy,
  "check my profile", predaj followerov, krypto/kasíno promo.
"ignore" – všetko ostatné: len emoji, označenie kamaráta ("@niekto pozri"), nezmyselný text,
  hádky medzi používateľmi, provokácie, politika, komentáre, na ktoré sa nedá rozumne odpovedať.

Pravidlá odpovedí:
- Po slovensky (ak píše po česky, odpovedz po slovensky; ak po anglicky, odpovedz po anglicky). Tykaj.
- Max 180 znakov, max 1 emoji, žiadne hashtagy, žiadne odkazy.
- Nič si nevymýšľaj. Žiadne dátumy, ceny ani fakty, ktoré nie sú v texte postu.
- Nikdy nesľubuj súťaže, darčeky, spoluprácu ani nič v mene stránky.
- Nehádaj sa, nezaujímaj politické ani náboženské postoje, nekritizuj konkrétnych ľudí.
- Ak sa niekto pýta, či je to bot/AI, odpovedz pravdivo: posty aj odpovede spravuje AI.
- Tón: {cfg["tone"].strip().splitlines()[0]}
- Nezačínaj každú odpoveď rovnako. Žiadne "Skvelá otázka!"."""


def _decide(post_caption: str, batch: list[dict], cfg: dict) -> list[dict]:
    listing = [{"id": c["id"], "user": c.get("username"), "text": c.get("text", "")[:500]} for c in batch]
    user = f"""Text postu:
{post_caption[:1500]}

Komentáre:
{json.dumps(listing, ensure_ascii=False)}

Vráť: {{"actions": [{{"id": "...", "action": "reply"|"hide"|"ignore", "reply": "text odpovede alebo null"}}]}}"""
    return ask_json(cfg["model"]["writer"], _policy(cfg), user, max_tokens=3000).get("actions", [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    ccfg = cfg.get("comments", {})
    if not cfg.get("enabled", True) or not ccfg.get("enabled", True):
        log.info("Komentáre sú vypnuté.")
        return

    ig = Instagram()
    me = ig.me().get("username", "").lower()
    state = load_state("comments", {"handled": []})
    handled = set(state["handled"])
    before = set(handled)
    cutoff = now_utc() - timedelta(days=ccfg.get("max_age_days", 7))
    budget = ccfg.get("max_replies_per_run", 25)
    stats = {"reply": 0, "hide": 0, "ignore": 0}

    for media in ig.recent_media():
        ts = datetime.strptime(media["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        if ts < cutoff or not media.get("comments_count"):
            continue
        fresh = []
        for c in ig.comments(media["id"]):
            if c["id"] in handled or c.get("hidden"):
                continue
            if (c.get("username") or "").lower() == me:
                handled.add(c["id"])
                continue
            replies = (c.get("replies") or {}).get("data", [])
            if any((r.get("username") or "").lower() == me for r in replies):
                handled.add(c["id"])
                continue
            fresh.append(c)
        if not fresh:
            continue

        by_id = {c["id"]: c for c in fresh}
        for i in range(0, len(fresh), 30):
            actions = _decide(media.get("caption") or "", fresh[i:i + 30], cfg)
            for a in actions:
                cid, act = a.get("id"), a.get("action")
                if cid not in by_id:
                    continue
                text = (a.get("reply") or "").strip()
                if act == "reply":
                    if budget <= 0:
                        continue  # nechaj na ďalší beh, neoznačuj ako vybavené
                    if not text or len(text) > 220 or URL_RE.search(text):
                        act = "ignore"
                if act == "hide" and not ccfg.get("hide_toxic", True):
                    act = "ignore"

                log.info("[%s] @%s: %s%s", act.upper(), by_id[cid].get("username"),
                         by_id[cid].get("text", "")[:80], f"  ->  {text}" if act == "reply" else "")
                if not args.dry_run:
                    try:
                        if act == "reply":
                            ig.reply(cid, text)
                            budget -= 1
                        elif act == "hide":
                            ig.hide(cid)
                    except Exception as e:  # noqa: BLE001
                        log.warning("Akcia %s na %s zlyhala: %s", act, cid, e)
                        continue
                    handled.add(cid)
                stats[act] = stats.get(act, 0) + 1

    if not args.dry_run:
        new_ids = [h for h in handled if h not in before]
        save_state("comments", {"handled": (state["handled"] + new_ids)[-8000:]})
    log.info("Hotovo: %s", stats)
    if stats.get("hide"):
        notify(f"🛡️ Skrytých toxických komentárov: {stats['hide']}, odpovedí: {stats['reply']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        notify(f"❌ Bot zlyhal pri komentároch: {e}")
        raise
