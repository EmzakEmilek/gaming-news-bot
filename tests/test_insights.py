from datetime import datetime, timedelta, timezone

from bot import insights as ins
from bot.common import load_state, save_state
from bot.instagram import IGError


def _posted(n=12):
    now = datetime.now(timezone.utc)
    return [{"media_id": str(i), "at": (now - timedelta(days=i * 0.5 + 0.1)).isoformat(), "headline": f"Post {i}",
             "category": "UPDATE", "permalink": f"p{i}", "slot": "rano" if i % 2 else "vecer", "cost_usd": 0.15}
            for i in range(n)]


class FakeIG:
    def followers_count(self):
        return 57

    def online_followers(self):
        return {}

    def media_insights(self, mid, metrics):
        i = int(mid)
        return {"reach": 20 + i, "likes": i % 4, "comments": 0, "shares": 1 if i == 7 else 0, "saved": 0,
                "follows": 2 if i == 9 else 0, "profile_visits": 1}


def test_collect_hint_and_report(cfg):
    save_state("posted", _posted())
    state, err = ins.collect(FakeIG())
    assert err is None and len(state["media"]) == 12 and state["followers"][-1]["count"] == 57
    hint = ins.performance_hint()
    assert "Post 9" in hint.split("najslabšie")[0]  # nový sledovateľ váži najviac
    text = ins.report(state, cfg)
    assert "Najviac nových sledovateľov:** Post 9 (+2)" in text
    assert "Náklady na Claude" in text and "Vynechané sloty:** 0" in text
    assert "od 100 sledovateľov" in text


def test_collect_without_permission(cfg):
    save_state("posted", _posted(2))

    class NoPerm(FakeIG):
        def media_insights(self, mid, metrics):
            raise IGError("Insufficient permission")
    state, err = ins.collect(NoPerm())
    assert err and "permission" in err


def test_best_hours_converted_to_slovak_time():
    # tichomorský čas 10 a 11 h = 19 a 20 h u nás (v lete)
    assert ins._best_hours({"date": "2026-09-28", "hours": {0: 3, 10: 9, 11: 12, 20: 5}}) == [5, 19, 20]


def test_report_counts_skipped_slots(cfg):
    save_state("skipped", [{"date": "2026-09-27", "slot": "vecer", "at": datetime.now(timezone.utc).isoformat(),
                            "reason": "žiadna správa neprešla overením"}])
    text = ins.report({"media": {}, "followers": []}, cfg)
    assert "Vynechané sloty:** 1" in text and "žiadna správa" in text
    assert load_state("skipped", [])
