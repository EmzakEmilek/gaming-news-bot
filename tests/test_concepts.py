from datetime import date, datetime, timedelta, timezone

from bot import concepts, render, sources
from bot.concepts import deal, quiz, radar, retro
from bot.concepts.common import caption_issues, used_keys

from PIL import Image


def test_schedule_only_in_concept_slot(cfg):
    tue = date(2026, 10, 20)
    assert concepts.scheduled(cfg, tue, "vecer") == "vs"
    assert concepts.scheduled(cfg, tue, "rano") is None
    off = {**cfg, "concepts": {**cfg["concepts"], "enabled": False}}
    assert concepts.scheduled(off, tue, "vecer") is None


def test_parse_games_keeps_first_release_and_ids():
    rows = [{"game": "http://www.wikidata.org/entity/Q1", "gameLabel": "Ōkami", "date": "2006-04-20T00:00:00Z", "sl": "40"},
            {"game": "http://www.wikidata.org/entity/Q1", "gameLabel": "Ōkami", "date": "2008-04-15T00:00:00Z", "sl": "40",
             "steam": "587620", "enwiki": "Ōkami"},
            {"game": "http://www.wikidata.org/entity/Q2", "gameLabel": "Q2", "date": "2010-01-01T00:00:00Z", "sl": "90"}]
    games = sources.parse_games(rows)
    assert len(games) == 1  # Q2 nemá anglický názov
    assert games[0]["first"] == date(2006, 4, 20) and games[0]["steam"] == "587620" and games[0]["enwiki"] == "Ōkami"


def test_sk_date_and_euro():
    assert sources.sk_date(date(2006, 4, 20)) == "20. apríla 2006"
    assert sources.sk_date(date(2026, 10, 1), year=False) == "1. októbra"
    assert sources.euro(749) == "7,49 €" and sources.euro(2999) == "29,99 €"


def test_steam_find_needs_matching_name(monkeypatch):
    monkeypatch.setattr(sources, "steam_search", lambda t: {"id": 1, "name": "Ghost of Tsushima DIRECTOR'S CUT"})
    assert sources.steam_find("Ghost of Tsushima")
    assert not sources.steam_find("Ghost of Yōtei")


def test_retro_prefers_round_anniversary():
    today = date(2026, 10, 21)
    games = [{"qid": "A", "first": date(2007, 10, 20), "sitelinks": 90, "enwiki": "A"},   # 19 rokov
             {"qid": "B", "first": date(2006, 10, 23), "sitelinks": 30, "enwiki": "B"},   # 20 rokov
             {"qid": "C", "first": date(2006, 11, 30), "sitelinks": 99, "enwiki": "C"},   # mimo okna
             {"qid": "D", "first": date(2024, 10, 21), "sitelinks": 99, "enwiki": "D"}]   # príliš nové
    game, years = retro.pick(games, set(), today)
    assert game["qid"] == "B" and years == 20
    assert retro.pick(games, {"retro:B", "retro:A"}, today) is None


def test_quiz_clues_never_reveal_name():
    game = {"name": "Hollow Knight", "first": date(2017, 2, 24)}
    app = {"genres": [{"description": "Action"}], "developers": ["Team Cherry"]}
    assert quiz.clues(game, app, {}) == ["Vyšla v roku 2017", "Žáner: akčná", "Vyvinulo ju štúdio Team Cherry"]
    game = {"name": "Team Cherry Adventure", "first": date(2017, 2, 24)}
    assert all("Cherry" not in c for c in quiz.clues(game, app, {}))


def test_quiz_pick_skips_used_and_new_games():
    today = date(2026, 10, 22)
    games = [{"qid": "A", "steam": "1", "first": date(2026, 3, 1)}, {"qid": "B", "steam": None, "first": date(2010, 1, 1)},
             {"qid": "C", "steam": "3", "first": date(2010, 1, 1)}, {"qid": "D", "steam": "4", "first": date(2011, 1, 1)}]
    assert quiz.pick(games, {"quiz:D"}, today)["qid"] == "C"


def test_deal_pick_needs_known_game_and_real_discount():
    specials = [{"id": 1}, {"id": 2}, {"id": 3}]
    apps = {1: {"price_overview": {"currency": "EUR", "discount_percent": 80}, "recommendations": {"total": 500}},
            2: {"price_overview": {"currency": "EUR", "discount_percent": 50}, "recommendations": {"total": 40000}},
            3: {"price_overview": {"currency": "EUR", "discount_percent": 20}, "recommendations": {"total": 900000}}}
    assert deal.pick(specials, apps, set())[0]["id"] == 2
    assert deal.pick(specials, apps, {"deal:2"}) is None


def test_radar_verified_needs_official_or_two_portals():
    by_id = {"a": {"source": "PlayStation Blog", "tier": "official"}, "b": {"source": "Push Square", "tier": "trusted"},
             "c": {"source": "VGC", "tier": "trusted"}}
    start, end = date(2026, 10, 19), date(2026, 10, 25)
    games = [{"title": "A", "date": "2026-10-21", "ids": ["a"]}, {"title": "B", "date": "2026-10-22", "ids": ["b"]},
             {"title": "C", "date": "2026-10-23", "ids": ["b", "c"]}, {"title": "D", "date": "2026-11-02", "ids": ["a"]},
             {"title": "a", "date": "2026-10-21", "ids": ["a"]}]
    assert [g["title"] for g in radar.verified(games, by_id, start, end)] == ["A", "C"]
    assert radar.platforms(["PlayStation 5", "Windows", "Xbox Series X|S", "Nintendo Switch 2"]) == ["PS5", "PC", "Xbox", "+1"]


def test_used_keys_only_recent_concept_posts():
    now = datetime.now(timezone.utc)
    posted = [{"at": now.isoformat(), "concept": "quiz", "key": "quiz:A"},
              {"at": (now - timedelta(days=400)).isoformat(), "concept": "quiz", "key": "quiz:B"},
              {"at": now.isoformat(), "key": "x"}]
    assert used_keys(posted, "quiz", 365) == {"quiz:A"}


def test_caption_hook_limit():
    assert caption_issues({"caption": "Krátky hook.\n\nĎalej."}) == []
    assert caption_issues({"caption": "A" * 130 + ". B"})


def test_crop_keeps_aspect_and_bounds():
    img = Image.new("RGB", (1920, 1080))
    out = render._crop(img, {"zoom": 4, "cx": .99, "cy": .99})
    assert abs(out.width / out.height - 1.5) < .02 and out.height == 270  # 4x menej ako výška fotky
    assert render._crop(img, {"zoom": 1}).height == 1080
