import sys

import bot.comments as cm
from bot.common import load_state


class FakeIG:
    user_id = "1"

    def me(self):
        return {"username": "emzo.daily"}

    def recent_media(self):
        return [{"id": "m1", "timestamp": "2099-01-01T00:00:00+0000", "comments_count": 4, "caption": "Post",
                 "permalink": "https://instagram.com/p/X"}]

    def comments(self, mid):
        return [{"id": "c1", "text": "Kedy to vyjde na PC?", "username": "hrac1", "from": {"id": "9"}},
                {"id": "c2", "text": "Si bot alebo človek?", "username": "hrac2", "from": {"id": "8"}},
                {"id": "c3", "text": "kup si followerov na mojom profile", "username": "spam", "from": {"id": "7"}},
                {"id": "c4", "text": "🔥🔥", "username": "x", "from": {"id": "6"}}]

    def reply(self, cid, text):
        return "r"

    def hide(self, cid):
        pass


def _cfg(auto_reply):
    real = cm.load_config
    def load():
        cfg = real()
        cfg["comments"]["auto_reply"] = auto_reply
        return cfg
    return load


def test_comments_report_to_discord(monkeypatch):
    monkeypatch.setattr(cm, "load_config", _cfg(True))
    sent = []
    monkeypatch.setattr(cm, "notify_long", sent.append)
    monkeypatch.setattr(cm, "Instagram", FakeIG)
    monkeypatch.setattr(cm, "_decide", lambda groups, cfg: [
        {"id": "c1", "action": "reply", "reply": "Na PC zatiaľ dátum nie je."},
        {"id": "c2", "action": "owner", "reply": ""},
        {"id": "c3", "action": "hide", "reply": ""}])
    monkeypatch.setattr(sys, "argv", ["comments"])
    cm.main()
    assert len(sent) == 1
    for part in ("Čaká na tvoju odpoveď (1)", "Bot odpovedal (1)", "Bot skryl (1)", "→ Na PC zatiaľ dátum nie je."):
        assert part in sent[0]
    assert set(load_state("comments", {})["handled"]) >= {"c1", "c2", "c3", "c4"}
    sent.clear()
    cm.main()  # nič nové -> žiadna správa
    assert sent == []


def test_without_auto_reply_owner_gets_suggestion(monkeypatch):
    monkeypatch.setattr(cm, "load_config", _cfg(False))
    sent, replies = [], []
    monkeypatch.setattr(cm, "notify_long", sent.append)
    monkeypatch.setattr(cm, "Instagram", FakeIG)
    monkeypatch.setattr(FakeIG, "reply", lambda self, cid, text: replies.append(cid))
    monkeypatch.setattr(cm, "_decide", lambda groups, cfg: [
        {"id": "c1", "action": "reply", "reply": "Na PC zatiaľ dátum nie je."},
        {"id": "c3", "action": "hide", "reply": ""}])
    monkeypatch.setattr(sys, "argv", ["comments"])
    cm.main()
    assert replies == []
    assert "Čaká na tvoju odpoveď (1)" in sent[0] and "návrh odpovede: Na PC zatiaľ dátum nie je." in sent[0]
    assert "Bot odpovedal" not in sent[0] and "Bot skryl (1)" in sent[0]


def test_trivial_comments():
    assert cm._trivial("🔥🔥") and cm._trivial("@kamos 😂") and not cm._trivial("@kamos pozri") and not cm._trivial("super hra")
