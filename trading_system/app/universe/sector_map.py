from __future__ import annotations

_BASE_TO_SECTOR: dict[str, str] = {
    "BTC": "majors",
    "ETH": "majors",
    "BNB": "majors",
    "SOL": "alt_l1",
    "ADA": "alt_l1",
    "AVAX": "alt_l1",
    "XRP": "payments",
    "XLM": "payments",
    "LINK": "oracle",
    "DOGE": "memes",
    "SHIB": "memes",
}

_KNOWN_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "USD")
_DEFAULT_SECTOR = "other_uncategorized"


def _base_asset(symbol: str) -> str:
    upper = str(symbol).upper().strip()
    for quote in _KNOWN_QUOTES:
        if upper.endswith(quote) and len(upper) > len(quote):
            return upper[: -len(quote)]
    return upper


def sector_for_symbol(symbol: str) -> str:
    base = _base_asset(symbol)
    return _BASE_TO_SECTOR.get(base, _DEFAULT_SECTOR)
