import pytest

from bot.instagram import IGError, Instagram


def _ig(fake):
    ig = Instagram.__new__(Instagram)
    ig.token, ig.user_id, ig._req = "t", "1", fake
    return ig


def test_publish_carousel_with_alt_texts_and_fallback():
    calls = []

    def fake(method, path, **p):
        calls.append((path, dict(p)))
        if path == "1/media" and "alt_text" in p and p.get("image_url") == "u2":
            raise IGError("alt_text nepodporovaný")
        if path == "1/media":
            return {"id": f"c{len(calls)}"}
        if path.endswith("media_publish"):
            return {"id": "M"}
        if p.get("fields") == "status_code":
            return {"status_code": "FINISHED"}
        return {"id": "M", "permalink": "P"}
    info = _ig(fake).publish(["u1", "u2", "u3"], "cap", alt_texts=["a1", "a2", "a3"])
    media = [p for path, p in calls if path == "1/media"]
    assert media[0]["alt_text"] == "a1"
    assert media[1]["alt_text"] == "a2" and "alt_text" not in media[2]  # pokus s alt textom, potom bez neho
    assert media[3]["alt_text"] == "a3" and media[4]["media_type"] == "CAROUSEL"
    assert info["permalink"] == "P"


def test_publish_story():
    calls = []

    def fake(method, path, **p):
        calls.append(p)
        if p.get("fields") == "status_code":
            return {"status_code": "FINISHED"}
        return {"id": "S"}
    assert _ig(fake).publish_story("s.jpg") == "S"
    assert calls[0]["media_type"] == "STORIES"


def test_media_insights_falls_back_to_single_metrics():
    def fake(method, path, **p):
        m = p["metric"]
        if "," in m or m == "views":
            raise IGError("neplatná metrika")
        return {"data": [{"name": m, "values": [{"value": 5}]}]}
    assert _ig(fake).media_insights("9", ["reach", "views", "likes"]) == {"reach": 5, "likes": 5}


def test_media_insights_without_permission_raises():
    def fake(*a, **k):
        raise IGError("chýba oprávnenie")
    with pytest.raises(IGError):
        _ig(fake).media_insights("9", ["reach"])


def test_online_followers():
    fake = lambda *a, **k: {"data": [{"values": [{"value": {"0": 3, "10": 9}}]}]}  # noqa: E731
    assert _ig(fake).online_followers() == {0: 3, 10: 9}


def test_publish_retries_when_media_not_ready(monkeypatch):
    import bot.instagram as igmod
    monkeypatch.setattr(igmod.time, "sleep", lambda s: None)
    tries = []

    def fake(method, path, **p):
        if path.endswith("media_publish"):
            tries.append(1)
            if len(tries) < 3:
                raise IGError("POST 1/media_publish: Media ID is not available (code 9007)")
            return {"id": "M"}
        if p.get("fields") == "status_code":
            return {"status_code": "FINISHED"}
        if path == "1/media":
            return {"id": "c"}
        return {"id": "M", "permalink": "P"}
    assert _ig(fake).publish(["u1"], "cap")["permalink"] == "P"
    assert len(tries) == 3


def test_publish_does_not_retry_other_errors(monkeypatch):
    import bot.instagram as igmod
    monkeypatch.setattr(igmod.time, "sleep", lambda s: None)

    def fake(method, path, **p):
        if path.endswith("media_publish"):
            raise IGError("POST 1/media_publish: Invalid parameter (code 100)")
        if p.get("fields") == "status_code":
            return {"status_code": "FINISHED"}
        return {"id": "c"}
    with pytest.raises(IGError):
        _ig(fake).publish(["u1"], "cap")
