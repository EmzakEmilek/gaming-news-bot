"""Hosting obrázkov na GitHub Pages (Instagram si ich stiahne z verejnej URL)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from .common import ROOT, log

BRANCH = "gh-pages"
TMP_BRANCH = "pages-upload"


def _git(*args: str, cwd: Path) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} zlyhal: {res.stderr.strip()}")
    return res.stdout.strip()


def pages_base_url() -> str:
    override = os.environ.get("PAGES_BASE_URL")
    if override:
        return override.rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise SystemExit("Chýba GITHUB_REPOSITORY alebo PAGES_BASE_URL.")
    owner, name = repo.split("/")
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io"
    return f"https://{owner.lower()}.github.io/{name}"


def upload(files: list[Path], run_id: str) -> list[str]:
    """Vetva gh-pages má vždy jediný commit len s aktuálnymi obrázkami (force push).
    Instagram si obrázky pri publikovaní skopíruje, staré netreba – a história tak nerastie."""
    tmp = Path(tempfile.mkdtemp(prefix="pages-"))
    wt = tmp / "wt"
    try:
        subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)
        subprocess.run(["git", "branch", "-D", TMP_BRANCH], cwd=ROOT, capture_output=True)
        _git("worktree", "add", "--orphan", "-b", TMP_BRANCH, str(wt), cwd=ROOT)
        (wt / ".nojekyll").touch()
        target = wt / "media" / run_id
        target.mkdir(parents=True)
        for f in files:
            shutil.copy2(f, target / f.name)
        _git("add", "-A", cwd=wt)
        _git("commit", "-m", f"media {run_id}", cwd=wt)
        _git("push", "--force", "origin", f"{TMP_BRANCH}:{BRANCH}", cwd=wt)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)
        subprocess.run(["git", "branch", "-D", TMP_BRANCH], cwd=ROOT, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)

    base = pages_base_url()
    urls = [f"{base}/media/{run_id}/{f.name}" for f in files]
    _wait_public(urls)
    return urls


def _wait_public(urls: list[str], timeout: int = 600) -> None:
    """GitHub Pages potrebuje chvíľu na nasadenie. Čakáme, kým sú všetky obrázky verejne dostupné."""
    deadline = time.time() + timeout
    pending = list(urls)
    while pending and time.time() < deadline:
        still = []
        for u in pending:
            try:
                r = requests.head(u, timeout=15, allow_redirects=True)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                    continue
            except requests.RequestException:
                pass
            still.append(u)
        pending = still
        if pending:
            time.sleep(15)
    if pending:
        raise RuntimeError(f"Obrázky nie sú verejne dostupné ani po {timeout}s: {pending[:2]}")
    log.info("Obrázky sú verejne dostupné (%d)", len(urls))
