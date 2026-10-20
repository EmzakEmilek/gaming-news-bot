import itertools
from pathlib import Path

import pytest

from bot import hosting


class _Resp:
    def __init__(self, ok: bool):
        self.status_code = 200 if ok else 404
        self.headers = {"content-type": "image/jpeg" if ok else "text/html"}


@pytest.fixture
def fake_push(monkeypatch):
    """upload bez gitu: commit má SHA "abc123", Pages URL je pevná, čakanie bez sleep."""
    def git(*args, cwd):
        if args[:2] == ("worktree", "add"):
            Path(args[-1]).mkdir(parents=True)
        return "abc123" if args[0] == "rev-parse" else ""
    monkeypatch.setattr(hosting, "_git", git)
    monkeypatch.setattr(hosting.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(hosting.time, "sleep", lambda s: None)
    monkeypatch.setenv("PAGES_BASE_URL", "https://pages.example")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")


def _patch_head(monkeypatch, public):
    """public(url) -> bool; každé čakanie spraví jedno kolo kontrol, potom vyprší timeout."""
    clock = (t for k in itertools.count() for t in (2000 * k, 2000 * k, 2000 * k + 1000))
    monkeypatch.setattr(hosting.time, "time", lambda: next(clock))
    monkeypatch.setattr(hosting.requests, "head", lambda url, **k: _Resp(public(url)))


def _file(tmp_path) -> Path:
    f = tmp_path / "slide_1.jpg"
    f.write_bytes(b"jpg")
    return f


def test_upload_returns_pages_urls_when_public(tmp_path, fake_push, monkeypatch):
    _patch_head(monkeypatch, lambda url: True)
    assert hosting.upload([_file(tmp_path)], "run") == ["https://pages.example/media/run/slide_1.jpg"]


def test_upload_falls_back_to_raw_when_pages_slow(tmp_path, fake_push, monkeypatch):
    _patch_head(monkeypatch, lambda url: url.startswith("https://raw.githubusercontent.com/"))
    assert hosting.upload([_file(tmp_path)], "run") == [
        "https://raw.githubusercontent.com/owner/repo/abc123/media/run/slide_1.jpg"]


def test_upload_fails_when_nothing_public(tmp_path, fake_push, monkeypatch):
    _patch_head(monkeypatch, lambda url: False)
    with pytest.raises(RuntimeError, match="nie sú verejne dostupné"):
        hosting.upload([_file(tmp_path)], "run")
