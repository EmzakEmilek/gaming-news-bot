from PIL import Image, ImageDraw

from bot import photos


def _img(w, h, seed=0):
    img = Image.new("RGB", (w, h), (20, 20, 20))
    d = ImageDraw.Draw(img)
    for i in range(12):  # jednoduchý vzor, aby mali rôzne obrázky rôzny odtlačok
        x = (i * 97 + seed * 311) % w
        d.rectangle([x, (i * 53 + seed * 71) % h, x + w // 6, h], fill=((i * 40 + seed * 90) % 255, 120, 200))
    return img


def test_candidates_order_and_body_flag():
    arts = [{"source": "IGN", "tier": "trusted", "image": "a.jpg", "images": [{"url": "b.jpg", "alt": "x"}]},
            {"source": "Xbox Wire", "tier": "official", "image": "c.jpg", "images": []}]
    assert [c["url"] for c in photos.candidates(arts)] == ["c.jpg", "a.jpg", "b.jpg"]
    assert [c["url"] for c in photos.candidates(arts, body=False)] == ["c.jpg", "a.jpg"]
    assert [c["url"] for c in photos.candidates(arts, "official")] == ["c.jpg"]
    assert photos.candidates(arts, "none") == []


def test_gather_filters_and_removes_duplicates(monkeypatch):
    base = _img(1600, 900, 1)
    imgs = {"main": base, "dup": base.resize((1200, 675)), "other": _img(1920, 1080, 2),
            "small": _img(600, 338, 3), "portrait": _img(900, 1400, 4), "banner": _img(2000, 400, 5)}
    monkeypatch.setattr(photos, "fetch", imgs.get)
    cands = [{"url": u, "source": "S", "official": False, "main": u == "main"} for u in imgs]
    got = photos.gather(cands)
    assert [p["url"] for p in got] == ["main", "other"]  # titulka je hlavný obrázok, duplikát a nevhodné vypadli


def test_label_uses_alt_or_file_name():
    assert photos.label({"url": "https://x.com/a.jpg", "alt": "Jason na pláži"}) == "Jason na pláži"
    assert photos.label({"url": "https://x.com/img/gta-6-vice_city-beach.jpg", "alt": ""}) == "gta vice city beach"
    assert photos.label({"url": "https://x.com/12345.jpg", "alt": ""}) == "bez popisu"


def test_body_image_repeated_in_another_story_is_skipped(monkeypatch):
    imgs = {"ad": _img(1280, 720, 7), "shot": _img(1920, 1080, 8), "shot2": _img(1920, 1080, 9)}
    monkeypatch.setattr(photos, "fetch", imgs.get)
    c = lambda u: {"url": u, "source": "S", "official": False}  # noqa: E731
    photos.gather([c("ad"), c("shot")])  # prvá správa: banner si zapamätá
    assert "ad" not in [p["url"] for p in photos.gather([c("ad"), c("shot2")])]  # banner webu z predošlej správy
    assert photos.SKIP_URL.search("https://www.sector.sk/img/sutaz-office.jpg")
