"""Tenký klient pre Instagram API (Instagram Login, graph.instagram.com)."""
from __future__ import annotations

import time

import requests

from .common import env, log

API = "https://graph.instagram.com/v25.0"


class IGError(RuntimeError):
    pass


class Instagram:
    def __init__(self, token: str | None = None, user_id: str | None = None):
        self.token = token or env("IG_ACCESS_TOKEN")
        self.user_id = user_id or env("IG_USER_ID", required=False)
        if not self.user_id:  # ID účtu vieme zistiť priamo z tokenu
            self.user_id = str(self._req("GET", "me", fields="user_id")["user_id"])

    def _req(self, method: str, path: str, **params):
        params["access_token"] = self.token
        for attempt in range(4):
            if method == "GET":
                r = requests.get(f"{API}/{path}", params=params, timeout=30)
            else:
                r = requests.post(f"{API}/{path}", data=params, timeout=60)
            data = r.json() if r.content else {}
            if r.ok and "error" not in data:
                return data
            err = data.get("error", {})
            transient = r.status_code >= 500 or err.get("is_transient") or err.get("code") in (1, 2, 4, 17, 341)
            if transient and attempt < 3:
                log.warning("IG API dočasná chyba, skúšam znova: %s", err.get("message"))
                time.sleep(10 * (attempt + 1))
                continue
            raise IGError(f"{method} {path}: {err.get('message', r.text)} (code {err.get('code')})")

    # ── publikovanie ─────────────────────────────────────────
    def _container(self, **params) -> str:
        return self._req("POST", f"{self.user_id}/media", **params)["id"]

    def _wait_ready(self, container_id: str, timeout: int = 300) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self._req("GET", container_id, fields="status_code").get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise IGError(f"Kontajner {container_id} skončil so stavom {status}")
            time.sleep(10)
        raise IGError(f"Kontajner {container_id} nie je pripravený ani po {timeout}s")

    def publish(self, image_urls: list[str], caption: str, ai_label: bool = False) -> dict:
        extra = {"is_ai_generated": "true"} if ai_label else {}
        if len(image_urls) == 1:
            cid = self._container(image_url=image_urls[0], caption=caption, **extra)
        else:  # najprv založiť všetky snímky, potom počkať na všetky naraz
            children = [self._container(image_url=url, is_carousel_item="true") for url in image_urls[:10]]
            for child in children:
                self._wait_ready(child)
            cid = self._container(media_type="CAROUSEL", children=",".join(children), caption=caption, **extra)
        self._wait_ready(cid)
        media_id = self._req("POST", f"{self.user_id}/media_publish", creation_id=cid)["id"]
        try:  # post už je vonku – chyba pri zisťovaní odkazu nesmie zhodiť beh (stav by sa neuložil)
            info = self._req("GET", media_id, fields="id,permalink,timestamp")
        except IGError as e:
            log.warning("Post %s je publikovaný, ale odkaz sa nepodarilo zistiť: %s", media_id, e)
            info = {"id": media_id}
        log.info("Publikované: %s", info.get("permalink") or media_id)
        return info

    def publishing_quota(self) -> dict:
        data = self._req("GET", f"{self.user_id}/content_publishing_limit", fields="quota_usage,config")
        return (data.get("data") or [{}])[0]

    # ── komentáre ────────────────────────────────────────────
    def me(self) -> dict:
        return self._req("GET", "me", fields="user_id,username")

    def recent_media(self, limit: int = 20) -> list[dict]:
        return self._req("GET", f"{self.user_id}/media",
                         fields="id,caption,timestamp,permalink,comments_count", limit=limit).get("data", [])

    def comments(self, media_id: str) -> list[dict]:
        out, params = [], {"fields": "id,text,username,timestamp,hidden,replies{id,username,text}", "limit": 50}
        path = f"{media_id}/comments"
        data = self._req("GET", path, **params)
        out.extend(data.get("data", []))
        # stránkovanie (max 4 strany = 200 komentárov na post za beh)
        for _ in range(3):
            nxt = data.get("paging", {}).get("next")
            if not nxt:
                break
            data = requests.get(nxt, timeout=30).json()
            out.extend(data.get("data", []))
        return out

    def reply(self, comment_id: str, message: str) -> str:
        return self._req("POST", f"{comment_id}/replies", message=message)["id"]

    def hide(self, comment_id: str) -> None:
        self._req("POST", comment_id, hide="true")

    # ── token ────────────────────────────────────────────────
    def refresh_token(self) -> dict:
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": self.token}, timeout=30)
        data = r.json()
        if "access_token" not in data:
            raise IGError(f"Obnova tokenu zlyhala: {data}")
        return data
