import copy

from bot import editor


def test_apply_fixes_replaces_and_reports_missing():
    post = {"headline": "H", "caption": "K dispozícii bude desiatky áut.", "cta": {"title": "C"},
            "slides": [{"title": "T", "body": "Hra vypadá z dálky super."}]}
    missed = editor._apply_fixes(post, [{"find": "bude desiatky", "replace": "budú desiatky"},
                                        {"find": "vypadá z dálky", "replace": "vyzerá z diaľky"},
                                        {"find": "neexistuje", "replace": "x"}])
    assert post["caption"] == "K dispozícii budú desiatky áut."
    assert post["slides"][0]["body"] == "Hra vyzerá z diaľky super."
    assert missed == ["Oprav „neexistuje“ na „x“."]


def _post(**kw):
    base = {"headline": "Nadpis", "category": "UPDATE", "cta": {"type": "share", "title": "Pošli to"},
            "caption": "Krátky hook.\n\nDruhý odsek.\n\nPošli to kamošovi.", "hashtags": ["#hry"],
            "slides": [{"title": "T1", "body": "Demo vyjde 1. októbra."}, {"title": "T2", "body": "Hra 15. októbra."}]}
    return {**base, **kw}


def test_shape_flags_relative_time_and_numeric_dates(cfg):
    issues = editor._validate_shape(_post(headline="Predobjednávky od zajtra", caption="Vyjde 1.10. a v pondelok."), cfg)
    text = " ".join(issues)
    assert "zajtra" in text and "v pondelok" in text and "1.10." in text


def test_shape_ignores_version_numbers_and_ordinals(cfg):
    assert editor._validate_shape(_post(caption="Patch 1.10 je vonku.\n\nVyjde 3. októbra.\n\nPošli to."), cfg) == []


def test_shape_caption_hook_length(cfg):
    long_first = "A" * 140 + ". Zvyšok."
    assert any("prvá veta captionu" in i for i in editor._validate_shape(_post(caption=long_first), cfg))


def test_produce_applies_fixes_without_rewrite(cfg, monkeypatch):
    calls = []
    written = _post(caption="Hra bude desiatky áut.\n\nDruhý odsek.\n\nPošli to kamošovi.")
    monkeypatch.setattr(editor, "_write", lambda *a, **k: calls.append("write") or copy.deepcopy(written))
    monkeypatch.setattr(editor, "_review", lambda *a, **k: calls.append("review") or {
        "blocking": [], "fixes": [{"find": "bude desiatky", "replace": "bude mať desiatky"}], "minor": []})

    def check(post, *a, **k):
        calls.append("check")
        assert "bude mať desiatky" in post["caption"]
        return {"blocking": [], "minor": []}
    monkeypatch.setattr(editor, "_check", check)
    monkeypatch.setattr(editor, "_polish_cta", lambda *a: calls.append("cta"))
    assert editor.produce("téma", [], cfg)
    assert calls == ["write", "review", "check", "cta"]


BY_ID = {"a": {"source": "IGN", "tier": "trusted"}, "b": {"source": "VGC", "tier": "trusted"},
         "c": {"source": "Xbox Wire", "tier": "official"}}


def test_rumor_needs_two_portals(cfg):
    assert editor._passes_verification({"is_rumor": True, "item_ids": ["a", "b"]}, BY_ID, cfg)[0]
    assert not editor._passes_verification({"is_rumor": True, "item_ids": ["a"]}, BY_ID, cfg)[0]
    assert not editor._passes_verification({"is_rumor": True, "item_ids": ["c"]}, BY_ID, cfg)[0]


def test_rumor_daily_limit_and_switch(cfg):
    ok, reason = editor._passes_verification({"is_rumor": True, "item_ids": ["a", "b"]}, BY_ID, cfg, rumors_left=False)
    assert not ok and "limit" in reason
    off = {**cfg, "posting": {**cfg["posting"], "allow_rumors": False}}
    assert not editor._passes_verification({"is_rumor": True, "item_ids": ["a", "b"]}, BY_ID, off)[0]


def test_official_source_is_enough(cfg):
    assert editor._passes_verification({"item_ids": ["c"]}, BY_ID, cfg)[0]


def test_finalize_marks_rumor(cfg):
    post = editor.finalize({"category": "UPDATE"}, "s", [], "fáma", cfg, rumor=True)
    assert post["category"] == "RUMOR" and post["rumor"]


def test_rumor_note_only_for_rumors(cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(editor, "ask_json", lambda model, system, user, **k: seen.append(user) or {"category": "UPDATE"})
    editor._write("x", [], cfg, rumor=True)
    editor._write("x", [], cfg, rumor=False)
    assert "RUMOR" in seen[0] and "RUMOR" not in seen[1]
