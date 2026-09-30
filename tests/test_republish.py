import json

from bot import republish


def test_load_post_skips_dry_runs_and_orders_slides(tmp_path):
    for name in ("2026-09-30-rano", "2026-09-30-rano-dry-101010"):
        d = tmp_path / name
        d.mkdir()
        (d / "post.json").write_text(json.dumps({"headline": name}), encoding="utf-8")
        for i in (1, 2, 10):
            (d / f"slide_{i}.jpg").write_bytes(b"")
    post, slides = republish.load_post(tmp_path)
    assert post["headline"] == "2026-09-30-rano"
    assert [p.name for p in slides] == ["slide_1.jpg", "slide_2.jpg", "slide_10.jpg"]


def test_replace_cta_changes_slide_and_last_caption_paragraph():
    post = {"caption": "Úvod.\n\nDetaily.\n\nAko ujdeš polícii?", "cta": {"type": "comment", "title": "Ako ujdeš polícii?"}}
    republish.replace_cta(post, "Máš už GTA 6 predobjednané?")
    assert post["cta"] == {"type": "comment", "title": "Máš už GTA 6 predobjednané?"}
    assert post["caption"] == "Úvod.\n\nDetaily.\n\nMáš už GTA 6 predobjednané?"
