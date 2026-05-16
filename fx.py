"""환율 변환. Frankfurter API (무료, 키 불필요)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen


_CACHE_FILE = Path(".fx_cache.json")
_CACHE_TTL = timedelta(hours=12)


def _load_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    _CACHE_FILE.write_text(json.dumps(data))


def get_rate(base: str, quote: str = "KRW") -> float:
    """1 base = ? quote. 12시간 캐시."""
    key = f"{base}_{quote}"
    cache = _load_cache()
    if key in cache:
        ts = datetime.fromisoformat(cache[key]["ts"])
        if datetime.now() - ts < _CACHE_TTL:
            return cache[key]["rate"]

    url = f"https://api.frankfurter.dev/v1/latest?base={base}&symbols={quote}"
    req = Request(url, headers={"User-Agent": "trip-optimizer/0.1"})
    with urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    rate = float(data["rates"][quote])

    cache[key] = {"rate": rate, "ts": datetime.now().isoformat()}
    _save_cache(cache)
    return rate


def to_krw(amount: float, currency: str) -> int:
    if currency.upper() == "KRW":
        return int(round(amount))
    return int(round(amount * get_rate(currency.upper(), "KRW")))
