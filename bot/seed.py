"""Jednorazové naplnenie profilu: publikuje najstarší pripravený post z priečinka seed/ (workflow Seed).

Každý podpriečinok seed/NN obsahuje snímky slide_*.jpg a post.json (final_caption, story, headline, links,
sources, format). Po publikovaní sa priečinok zmaže a post sa zapíše do state/posted.json.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta

from .common import ROOT, load_state, log, notify, now_local, now_utc, save_state

SEED_DIR = ROOT / "seed"


def main() -> bool:
    """Vráti False, keď je fronta prázdna (workflow sa potom sám vypne)."""
    queue = sorted(d for d in SEED_DIR.iterdir() if d.is_dir()) if SEED_DIR.exists() else []
    if not queue:
        log.info("Fronta seed/ je prázdna.")
        return False
    posted = load_state("posted", [])
    last_at = max((datetime.fromisoformat(p["at"]) for p in posted if p.get("at")), default=None)
    if last_at and now_utc() - last_at < timedelta(minutes=45):  # dva spúšťače v tej istej hodine = 1 post
        log.info("Posledný post bol pred menej ako 45 min, tento beh vynechávam.")
        return True

    from .hosting import upload
    from .instagram import Instagram

    d = queue[0]
    meta = json.loads((d / "post.json").read_text(encoding="utf-8"))
    files = sorted(d.glob("slide_*.jpg"))
    now = now_local()
    run_id = f"{now.date().isoformat()}-seed-{d.name}"
    info = Instagram().publish(upload(files, run_id), meta["final_caption"])

    posted.append({
        "date": now.date().isoformat(), "slot": f"seed-{d.name}", "at": now_utc().isoformat(),
        "story": meta["story"], "headline": meta["headline"], "links": meta["links"],
        "sources": meta["sources"], "format": meta["format"], "media_id": info["id"],
        "permalink": info.get("permalink"), "cost_usd": 0,
    })
    save_state("posted", posted[-500:])
    shutil.rmtree(d)
    log.info("Seed %s publikovaný, vo fronte ostáva %d.", d.name, len(queue) - 1)
    return True


if __name__ == "__main__":
    try:
        has_more = main()
    except Exception as e:
        notify(f"❌ Seed post zlyhal: {e}")
        raise
    if not has_more:
        raise SystemExit(3)  # workflow podľa tohto kódu vypne sám seba
