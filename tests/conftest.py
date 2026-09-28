"""Spoločné nastavenie testov: stav v dočasnom priečinku, žiadne notifikácie ani volania Claude."""
import pytest

import bot.common as common


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "STATE_DIR", tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    for var in ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.fixture
def cfg():
    return common.load_config()
