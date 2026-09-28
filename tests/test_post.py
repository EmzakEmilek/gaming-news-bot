import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import bot.post as post_mod
from bot.common import load_state, save_state

TZ = ZoneInfo("Europe/Bratislava")


def _utc(d, hh, mm):
    return datetime(*d, hh, mm, tzinfo=timezone.utc).astimezone(TZ)


def test_backup_cron_only_after_slot_summer_and_winter(cfg):
    crons = [(9, 40), (10, 40), (16, 40), (17, 40)]
    assert [post_mod._in_slot_window(_utc((2026, 9, 30), h, m), cfg) for h, m in crons] == [True] * 4
    assert [post_mod._in_slot_window(_utc((2026, 11, 4), h, m), cfg) for h, m in crons] == [False, True, False, True]


def test_slot_window_edges(cfg):
    local = lambda h, m: datetime(2026, 11, 4, h, m, tzinfo=TZ)  # noqa: E731
    assert not post_mod._in_slot_window(local(11, 29), cfg) and post_mod._in_slot_window(local(11, 30), cfg)
    assert post_mod._in_slot_window(local(12, 45), cfg) and not post_mod._in_slot_window(local(12, 46), cfg)


def test_alt_texts():
    post = {"category": "RUMOR", "headline": "H", "photo_credit": "IGN", "cta": {"title": "C"},
            "slides": [{"title": "T1", "body": "B1"}]}
    assert post_mod.alt_texts(post) == ["RUMOR: H. Fotka: IGN", "T1. B1", "C"]


POST = {"headline": "Nadpis", "story": "s"}


def test_failed_publish_is_saved_for_retry(cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Instagram nedostupný")
    monkeypatch.setattr(post_mod, "_publish", boom)
    monkeypatch.setattr(post_mod, "notify", lambda m: None)
    with pytest.raises(RuntimeError):
        post_mod.publish_post(dict(POST), cfg, None, "2026-09-29", "rano", [], dry_run=False)
    pending = load_state("pending", None)
    assert pending["slot"] == "rano" and pending["post"]["headline"] == "Nadpis"


def test_published_post_is_never_saved_for_retry(cfg, monkeypatch):
    def boom_after_publish(post, *a, **k):
        post["_published"] = True
        raise RuntimeError("uloženie stavu zlyhalo")
    monkeypatch.setattr(post_mod, "_publish", boom_after_publish)
    monkeypatch.setattr(post_mod, "notify", lambda m: None)
    with pytest.raises(RuntimeError):
        post_mod.publish_post(dict(POST), cfg, None, "2026-09-29", "rano", [], dry_run=False)
    assert load_state("pending", None) is None


def _run_main(monkeypatch, now):
    monkeypatch.setattr(post_mod, "now_local", lambda: now)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.setattr(sys, "argv", ["post"])
    post_mod.main()


def test_pending_post_is_published_without_writing(monkeypatch):
    now = datetime(2026, 9, 29, 11, 40, tzinfo=TZ)
    save_state("pending", {"date": "2026-09-29", "slot": "rano", "at": "x", "post": dict(POST)})
    published = []
    monkeypatch.setattr(post_mod, "publish_post", lambda p, *a: published.append(p["headline"]))
    monkeypatch.setattr(post_mod, "collect", lambda *a: pytest.fail("nemá sa písať nový post"))
    _run_main(monkeypatch, now)
    assert published == ["Nadpis"]


def test_stale_pending_is_dropped(monkeypatch):
    now = datetime(2026, 9, 29, 18, 35, tzinfo=TZ)
    save_state("pending", {"date": "2026-09-29", "slot": "rano", "at": "x", "post": dict(POST)})
    sent = []
    monkeypatch.setattr(post_mod, "notify", sent.append)
    monkeypatch.setattr(post_mod, "performance_hint", lambda: "")
    monkeypatch.setattr(post_mod, "collect", lambda *a: [])
    monkeypatch.setattr(post_mod, "make_post", lambda *a, **k: None)
    _run_main(monkeypatch, now)
    assert load_state("pending", None) is None
    assert any("prepadol" in m for m in sent)
    assert load_state("skipped", [])[-1]["slot"] == "vecer"
