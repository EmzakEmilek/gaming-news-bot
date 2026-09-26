"""Volanie Claude API s odpoveďou v JSON."""
from __future__ import annotations

import json
import re
import time

import anthropic

from .common import env, load_config, log, record_cost

# Strop na odpoveď. Claude Sonnet 5 má adaptívne thinking zapnuté a myslenie sa počíta do max_tokens,
# preto nízky limit môže odrezať JSON. Platí sa len za tokeny, ktoré model reálne vygeneruje.
MIN_MAX_TOKENS = 16000

_client: anthropic.Anthropic | None = None
USAGE: dict[str, dict[str, int]] = {}  # spotreba tokenov v tomto behu: model -> {"in", "out", "cache_write", "cache_read"}


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), max_retries=4)
    return _client


def _extract_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("V odpovedi nie je JSON")
    try:
        obj, _ = json.JSONDecoder(strict=False).raw_decode(text[start:])
    except json.JSONDecodeError:  # model občas pošle neplatný escape (napr. \'), zdvojíme osamotené lomítka
        fixed = re.sub(r"\\(.)", lambda m: m.group(0) if m.group(1) in '"\\/bfnrtu'
                       else "'" if m.group(1) == "'" else "\\\\" + m.group(1), text[start:], flags=re.S)
        obj, _ = json.JSONDecoder(strict=False).raw_decode(fixed)
    return obj


def run_cost() -> float:
    """Odhad ceny všetkých volaní v tomto behu v USD podľa api_prices v config.yaml.
    Zápis do cache stojí 1,25x cenu vstupu, čítanie z cache 0,1x."""
    prices = load_config().get("api_prices", {})
    total = 0.0
    for model, used in USAGE.items():
        price_in, price_out = prices.get(model, (0, 0))
        tokens_in = used["in"] + 1.25 * used["cache_write"] + 0.1 * used["cache_read"]
        total += tokens_in / 1e6 * price_in + used["out"] / 1e6 * price_out
    return total


def report_cost(kind: str) -> float:
    """Zaloguje cenu behu a pripočíta ju do mesačného súčtu (state/costs.json)."""
    usd = run_cost()
    if usd:
        log.info("Náklady na Claude API v tomto behu: ~$%.3f", usd)
        record_cost(kind, usd)
    return usd


def schema(**props) -> dict:
    """JSON schéma objektu pre structured outputs (všetky polia povinné, nič navyše)."""
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


STR = {"type": "string"}
STR_LIST = {"type": "array", "items": STR}


def ask_json(model: str, system: str, user: str, fmt: dict | None = None, cached: str | None = None,
             effort: str | None = None, max_tokens: int = MIN_MAX_TOKENS):
    """fmt: JSON schéma odpovede (structured outputs – API garantuje platný JSON).
    cached: začiatok user správy, ktorý sa v behu opakuje (zdroje) – ide do prompt cache.
    effort: hĺbka uvažovania ("low" / "medium" / "high" / "xhigh"), None = predvolené (high)."""
    content = user
    if cached:
        content = [{"type": "text", "text": cached, "cache_control": {"type": "ephemeral"}},
                   {"type": "text", "text": user}]
    output_config = {}
    if effort:
        output_config["effort"] = effort
    if fmt:
        output_config["format"] = {"type": "json_schema", "schema": fmt}
    extra = {"output_config": output_config} if output_config else {}
    last_err = None
    for attempt in range(3):
        resp = client().messages.create(
            model=model,
            max_tokens=max(max_tokens, MIN_MAX_TOKENS),
            system=system + ("" if fmt else "\n\nOdpovedz VÝHRADNE platným JSON objektom, bez ďalšieho textu."),
            messages=[{"role": "user", "content": content}],
            **extra,
        )
        u = resp.usage
        cache_write, cache_read = u.cache_creation_input_tokens or 0, u.cache_read_input_tokens or 0
        log.info("Claude %s%s: %d in (+%d cache zápis, %d z cache) / %d out tokenov (%s)", model,
                 f" [{effort}]" if effort else "", u.input_tokens, cache_write, cache_read, u.output_tokens,
                 resp.stop_reason)
        used = USAGE.setdefault(model, {"in": 0, "out": 0, "cache_write": 0, "cache_read": 0})
        used["in"] += u.input_tokens
        used["out"] += u.output_tokens  # obsahuje aj tokeny thinkingu
        used["cache_write"] += cache_write
        used["cache_read"] += cache_read
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"Model odmietol požiadavku ({resp.stop_details})")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            if resp.stop_reason == "max_tokens":
                raise ValueError("odpoveď bola orezaná (max_tokens)")
            return _extract_json(text)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            log.warning("Neplatný JSON od modelu (pokus %d): %s", attempt + 1, e)
            time.sleep(2)
    raise RuntimeError(f"Model nevrátil platný JSON: {last_err}")
