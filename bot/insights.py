"""Štatistiky postov: denne zbiera dosah, zdieľania a uloženia, v pondelok pošle týždenný prehľad na Discord/Telegram.
Výsledky dostáva aj výber tém ako jemný signál, čo u sledovateľov funguje.

Potrebuje oprávnenie tokenu instagram_business_manage_insights. Bez neho sa nič nezbiera, ostatné beží ďalej.

Použitie:
  python -m bot.insights            # len zber (bez notifikácie)
  python -m bot.insights --report   # zber + týždenný prehľad
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .common import TZ, load_config, load_state, log, notify, notify_long, now_local, now_utc, save_state

METRICS = ["reach", "views", "likes", "comments", "shares", "saved"]
REFRESH_DAYS = 14   # starší post už čísla takmer nemení, nepýtame sa na ne znova
HINT_DAYS = 30      # z akého obdobia sa počíta signál pre výber tém
MIN_AGE_H = 48      # čerstvý post ešte nemá dozbierané čísla
# online_followers vracia hodiny v tichomorskom čase (dokumentácia Meta), prehľad ich prevádza na slovenský čas
META_TZ = ZoneInfo("America/Los_Angeles")


def score(m: dict) -> float:
    """Interakcie na jedného osloveného. Zdieľanie a uloženie vážia viac (najsilnejšie signály pre dosah)."""
    reach = m.get("reach") or 0
    if not reach:
        return 0.0
    return (3 * m.get("shares", 0) + 2 * m.get("saved", 0) + 2 * m.get("comments", 0) + m.get("likes", 0)) / reach


def collect(ig) -> tuple[dict, str | None]:
    """Aktualizuje state/insights.json. Vráti (stav, chyba) – chyba je text, keď štatistiky nie sú dostupné."""
    state = load_state("insights", {"media": {}, "followers": []})
    try:
        count = ig.followers_count()
        if count is not None:
            today = now_local().date().isoformat()
            state["followers"] = [f for f in state["followers"] if f["date"] != today] + [{"date": today, "count": count}]
            state["followers"] = state["followers"][-400:]
    except Exception as e:  # noqa: BLE001
        log.warning("Počet sledovateľov sa nepodarilo zistiť: %s", e)
    try:  # Instagram ukazuje až od 100 sledovateľov
        online = ig.online_followers()
        state["online"] = {"date": now_local().date().isoformat(), "hours": online} if online else None
    except Exception as e:  # noqa: BLE001
        state["online"] = None
        log.info("Aktivita sledovateľov zatiaľ nie je dostupná: %s", e)

    now = now_utc()
    for p in load_state("posted", []):
        if not p.get("media_id") or not p.get("at"):
            continue
        age = now - datetime.fromisoformat(p["at"])
        known = state["media"].get(p["media_id"])
        if age > timedelta(days=REFRESH_DAYS) and known:
            continue
        if age > timedelta(days=HINT_DAYS):
            continue
        try:
            metrics = ig.media_insights(p["media_id"], METRICS)
        except Exception as e:  # noqa: BLE001 – typicky chýbajúce oprávnenie; platí pre všetky posty
            log.warning("Štatistiky postu %s nie sú dostupné: %s", p["media_id"], e)
            save_state("insights", state)
            return state, str(e)
        state["media"][p["media_id"]] = {
            "at": p["at"], "headline": p.get("headline"), "category": p.get("category"),
            "permalink": p.get("permalink"), "metrics": metrics, "updated": now.isoformat(),
        }
    save_state("insights", state)
    log.info("Štatistiky aktualizované (%d postov).", len(state["media"]))
    return state, None


def performance_hint() -> str:
    """Krátky text pre výber tém: ktoré posty za posledných 30 dní fungovali najlepšie a najhoršie."""
    media = load_state("insights", {}).get("media", {})
    now = now_utc()
    rows = [m for m in media.values()
            if timedelta(hours=MIN_AGE_H) <= now - datetime.fromisoformat(m["at"]) <= timedelta(days=HINT_DAYS)
            and (m["metrics"].get("reach") or 0) >= 10]
    if len(rows) < 6:  # na menej postov je to len šum
        return ""
    rows.sort(key=lambda m: score(m["metrics"]), reverse=True)
    fmt = lambda ms: "; ".join(f'"{m["headline"]}"' + (f' ({m["category"]})' if m.get("category") else "")  # noqa: E731
                               for m in ms)
    return (f"\nAko u našich sledovateľov fungovali posty za posledných {HINT_DAYS} dní (zdieľania, uloženia a komentáre"
            " na osloveného človeka). Je to len jemný signál: dôležitosť a čerstvosť správy majú prednosť,"
            " slabú správu nevyberaj len preto, že je z obľúbenej kategórie.\n"
            f"- najlepšie: {fmt(rows[:3])}\n- najslabšie: {fmt(rows[-3:])}\n")


def _followers_delta(followers: list[dict], days: int = 7) -> tuple[int | None, int | None]:
    if not followers:
        return None, None
    now_count = followers[-1]["count"]
    cutoff = (date.fromisoformat(followers[-1]["date"]) - timedelta(days=days)).isoformat()
    older = [f for f in followers if f["date"] <= cutoff]
    base = older[-1] if older else followers[0]
    return now_count, now_count - base["count"]


def _best_hours(online: dict | None) -> list[int]:
    """3 hodiny (slovenský čas), keď je online najviac sledovateľov."""
    if not online or not online.get("hours"):
        return []
    day = date.fromisoformat(online["date"])
    local = {}
    for h, n in online["hours"].items():
        t = datetime(day.year, day.month, day.day, int(h), tzinfo=META_TZ).astimezone(TZ)
        local[t.hour] = n
    return sorted(sorted(local, key=local.get, reverse=True)[:3])


def _timing(state: dict, cfg: dict) -> list[str]:
    """Kedy postovať: aktivita sledovateľov (od 100 sledovateľov) a priemerný dosah ranných a večerných postov."""
    lines = ["\n**⏰ Časy postov** (teraz " + " a ".join(cfg["posting"]["slot_times"]) + "):"]
    best = _best_hours(state.get("online"))
    if best:
        lines.append("Najviac sledovateľov je online o " + ", ".join(f"{h}:00" for h in best) + ".")
    else:
        lines.append("Aktivitu sledovateľov Instagram ukáže až od 100 sledovateľov.")
    slot_of = {p["media_id"]: p.get("slot") for p in load_state("posted", []) if p.get("media_id")}
    now = now_utc()
    by_slot: dict[str, list[int]] = {}
    for mid, m in state["media"].items():
        if slot_of.get(mid) in ("rano", "vecer") and \
                timedelta(hours=MIN_AGE_H) <= now - datetime.fromisoformat(m["at"]) <= timedelta(days=HINT_DAYS):
            by_slot.setdefault(slot_of[mid], []).append(m["metrics"].get("reach", 0))
    if all(len(by_slot.get(k, [])) >= 3 for k in ("rano", "vecer")):
        avg = {k: round(sum(v) / len(v)) for k, v in by_slot.items()}
        lines.append(f"Priemerne oslovení za 30 dní: ranné posty {avg['rano']}, večerné {avg['vecer']}.")
    else:
        lines.append("Porovnanie ranných a večerných postov bude po aspoň 3 postoch v každom slote.")
    return lines


def _costs_and_skips() -> list[str]:
    """Náklady na Claude a vynechané sloty za posledný týždeň."""
    now = now_utc()
    week = [p for p in load_state("posted", []) if p.get("at") and now - datetime.fromisoformat(p["at"]) <= timedelta(days=7)
            and not str(p.get("slot", "")).startswith("seed")]
    lines = ["\n**💰 Náklady na Claude:**"]
    if week:
        total = sum(p.get("cost_usd") or 0 for p in week)
        lines.append(f"Posty za týždeň: ${total:.2f} ({len(week)} postov, priemer ${total / len(week):.2f}).")
    month = load_state("costs", {}).get(now_local().strftime("%Y-%m"), {})
    if month:
        lines.append(f"Tento mesiac spolu (posty, komentáre, testy): ${month.get('total_usd', 0):.2f}.")
    skipped = [s for s in load_state("skipped", []) if now - datetime.fromisoformat(s["at"]) <= timedelta(days=7)]
    lines.append(f"\n**⏭️ Vynechané sloty:** {len(skipped)}")
    lines += [f"• {s['date']} {s['slot']}: {s['reason']}" for s in skipped[-5:]]
    return lines


def report(state: dict, cfg: dict) -> str:
    now = now_utc()
    week = [m for m in state["media"].values() if now - datetime.fromisoformat(m["at"]) <= timedelta(days=7)]
    start = (now_local() - timedelta(days=7)).date()
    lines = [f"📊 **{cfg['brand']['name']}** – týždeň {start.day}. {start.month}. – {now_local().day}. {now_local().month}."]
    count, delta = _followers_delta(state.get("followers", []))
    if count is not None:
        lines.append(f"Sledovatelia: **{count}** ({delta:+d} za týždeň)")
    if not week:
        lines.append("Za posledný týždeň nie sú žiadne štatistiky postov.")
        return "\n".join(lines + _timing(state, cfg) + _costs_and_skips())
    total = lambda k: sum(m["metrics"].get(k, 0) for m in week)  # noqa: E731
    lines.append(f"Posty: {len(week)} | oslovení spolu {total('reach')} | zdieľania {total('shares')}"
                 f" | uloženia {total('saved')} | komentáre {total('comments')} | lajky {total('likes')}")
    week.sort(key=lambda m: score(m["metrics"]), reverse=True)

    def row(m: dict) -> str:
        x = m["metrics"]
        return (f"• {m['headline']} – oslovení {x.get('reach', 0)}, zdieľania {x.get('shares', 0)},"
                f" uloženia {x.get('saved', 0)}, lajky {x.get('likes', 0)} <{m.get('permalink') or ''}>")
    lines.append("\n**Najlepšie:**")
    lines += [row(m) for m in week[:3]]
    if len(week) > 3:
        lines.append("\n**Najslabšie:**")
        lines += [row(m) for m in week[-2:]]
    return "\n".join(lines + _timing(state, cfg) + _costs_and_skips())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="poslať týždenný prehľad")
    args = ap.parse_args()
    cfg = load_config()

    from .instagram import Instagram
    state, error = collect(Instagram())
    if not args.report:
        return
    if error:
        notify(f"📊 Štatistiky nie sú dostupné – tokenu chýba oprávnenie instagram_business_manage_insights? ({error[:300]})")
        return
    text = report(state, cfg)
    log.info("\n%s", text)
    notify_long(text)


if __name__ == "__main__":
    main()
