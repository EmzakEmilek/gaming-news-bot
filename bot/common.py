"""Spoločné veci: konfigurácia, stav, logovanie, notifikácie."""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
OUT_DIR = ROOT / "out"
TEMPLATES_DIR = ROOT / "templates"
TZ = ZoneInfo("Europe/Bratislava")

try:  # lokálne kľúče z .env; na GitHube prichádzajú ako secrets v premenných prostredia
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):  # Windows konzola: diakritika a emoji v logoch
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("bot")


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def env(name: str, required: bool = True) -> str | None:
    val = os.environ.get(name)
    if required and not val:
        raise SystemExit(f"Chýba premenná prostredia {name} (GitHub secret).")
    return val


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    return datetime.now(TZ)


def load_state(name: str, default):
    path = STATE_DIR / f"{name}.json"
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(name: str, data) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    path = STATE_DIR / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def notify(message: str) -> None:
    """Voliteľná notifikácia na Discord/Telegram. Nikdy nezhodí beh."""
    discord = os.environ.get("DISCORD_WEBHOOK_URL")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
    try:
        if discord:
            requests.post(discord, json={"content": message[:1900]}, timeout=15)
        if tg_token and tg_chat:
            requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={"chat_id": tg_chat, "text": message[:4000], "disable_web_page_preview": True},
                timeout=15,
            )
    except Exception as e:  # noqa: BLE001
        log.warning("Notifikácia zlyhala: %s", e)
