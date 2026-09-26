"""Obnoví Instagram token (platí 60 dní) a uloží nový do GitHub secretu IG_ACCESS_TOKEN.

Beží automaticky každý týždeň. Potrebuje secret GH_PAT (fine-grained token s právom
"Secrets: Read and write" na tento repozitár).
"""
from __future__ import annotations

import os
import subprocess

from .common import env, log, notify
from .instagram import Instagram


def main() -> None:
    ig = Instagram()
    data = ig.refresh_token()
    new_token, days = data["access_token"], int(data.get("expires_in", 0)) // 86400
    subprocess.run(
        ["gh", "secret", "set", "IG_ACCESS_TOKEN", "--repo", env("GITHUB_REPOSITORY"), "--body", new_token],
        check=True, env={**os.environ, "GH_TOKEN": env("GH_PAT")}, capture_output=True,
    )
    log.info("Token obnovený, platí ďalších %d dní.", days)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        notify(f"❌ Obnova IG tokenu zlyhala – do vypršania treba vygenerovať nový token ručne! ({e})")
        raise
