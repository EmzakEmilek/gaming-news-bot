from PIL import Image

import bot.common as common
from bot import render


def test_typo_keeps_ordinal_with_word():
    assert render._typo("Demo vyjde 1. októbra a 3. séria") == "Demo vyjde 1. októbra a 3. séria"


def test_best_photo_prefers_official_then_resolution(tmp_path, monkeypatch):
    imgs = {"a": Image.new("RGB", (1200, 675)), "b": Image.new("RGB", (1920, 1080)),
            "c": Image.new("RGB", (700, 400)), "d": Image.new("RGB", (1000, 562))}
    monkeypatch.setattr(render, "_fetch_image", imgs.get)
    dest = tmp_path / "s.jpg"
    assert render._download_image([{"url": "a", "source": "IGN"}, {"url": "b", "source": "VGC"}], dest)[1] == "VGC"
    assert render._download_image([{"url": "b", "source": "VGC"},
                                   {"url": "d", "source": "Xbox Wire", "official": True}], dest)[1] == "Xbox Wire"
    assert render._download_image([{"url": "c"}, {"url": "chýba"}], dest) == (None, None)


def test_story_size(tmp_path):
    Image.new("RGB", (1080, 1350), (200, 30, 30)).save(tmp_path / "slide_1.jpg")
    out = render.render_story(tmp_path / "slide_1.jpg", tmp_path / "story.jpg")
    assert Image.open(out).size == (1080, 1920)


def test_notify_long_splits(monkeypatch):
    sent = []
    monkeypatch.setattr(common, "notify", sent.append)
    common.notify_long("\n".join(["x" * 300] * 12))
    assert len(sent) >= 2 and all(len(m) <= 1900 for m in sent)
