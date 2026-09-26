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
KEEP_RUNS = 20  # IG si obrázok skopíruje pri publikovaní, staré netreba držať


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
    tmp = Path(tempfile.mkdtemp(prefix="pages-"))
    try:
        subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)
        remote_has_branch = bool(_git("ls-remote", "--heads", "origin", BRANCH, cwd=ROOT))
        subprocess.run(["git", "branch", "-D", BRANCH], cwd=ROOT, capture_output=True)
        if remote_has_branch:
            _git("fetch", "origin", f"{BRANCH}:{BRANCH}", "--force", cwd=ROOT)
            _git("worktree", "add", str(tmp / "wt"), BRANCH, cwd=ROOT)
        else:
            _git("worktree", "add", "--orphan", "-b", BRANCH, str(tmp / "wt"), cwd=ROOT)
        wt = tmp / "wt"
        (wt / ".nojekyll").touch()
        media = wt / "media"
        media.mkdir(exist_ok=True)

        # upratanie starých behov
        runs = sorted([d for d in media.iterdir() if d.is_dir()], key=lambda d: d.name)
        for old in runs[:-KEEP_RUNS]:
            shutil.rmtree(old)

        target = media / run_id
        target.mkdir(exist_ok=True)
        for f in files:
            shutil.copy2(f, target / f.name)

        _git("add", "-A", cwd=wt)
        _git("commit", "-m", f"media {run_id}", cwd=wt)
        for attempt in range(3):
            try:
                _git("push", "origin", BRANCH, cwd=wt)
                break
            except RuntimeError:
                if attempt == 2:
                    raise
                _git("pull", "--rebase", "origin", BRANCH, cwd=wt)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tmp / "wt")], cwd=ROOT, capture_output=True)
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
