"""SerpAPI (Google Flights / Google Hotels) 어댑터.

사용 전 준비:
  1. https://serpapi.com 가입
  2. Dashboard에서 API key 복사
  3. .env 또는 환경변수로 주입:
       SERPAPI_KEY=xxx

전략:
  - 항공편: 왕복(type=1) 대신 편도(type=2) 검색 2회로 outbound/inbound를 독립적으로 모음.
    SerpAPI의 round-trip은 departure_token 기반 2단계 호출이 필요해 호출수가 많아지므로
    편도 × 2 방식이 호출수·시간창 필터링·매칭 자유도 모두 유리.
  - 호텔: Google Hotels 검색. 가격은 (rooms × nights) 합산이므로 1박/객실 단가로 환산.
  - 같은 요청은 SQLite 캐시(12h TTL)로 재사용 → 무료 한도(100/월) 보호.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from ..cache import Cache
from ..models import Flight, Hotel
from .base import FlightAdapter, HotelAdapter


_BASE = "https://serpapi.com/search.json"


# ---- 공통 ----------------------------------------------------------------

def _get_key() -> str:
    # .env 자동 로딩 (의존성 없이 직접 파싱)
    if not os.environ.get("SERPAPI_KEY"):
        env_path = ".env"
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        raise RuntimeError("SERPAPI_KEY 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")
    return key


def _api_get(params: Dict, cache: Optional[Cache] = None) -> Dict:
    """SerpAPI GET. cache가 주어지면 응답을 캐시(키 자체에서 api_key 제거)."""
    cache_key = None
    if cache is not None:
        scrubbed = {k: v for k, v in params.items() if k != "api_key"}
        cache_key = "serpapi:" + urlencode(sorted(scrubbed.items()))
        hit = cache.get(cache_key)
        if hit is not None:
            return hit

    url = f"{_BASE}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "trip-optimizer/0.2"})
    try:
        with urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except HTTPError as e:
        try:
            body = e.read().decode()
        except Exception:
            body = ""
        raise RuntimeError(f"SerpAPI HTTP {e.code}: {body[:300]}") from e

    if "error" in data:
        raise RuntimeError(f"SerpAPI error: {data['error']}")

    if cache is not None and cache_key is not None:
        cache.set(cache_key, data)
    return data


# ---- Flight --------------------------------------------------------------

# Google Flights `stops` 파라미터: 1=any, 2=nonstop, 3=≤1 stop
def _stops_param(max_stops: int) -> int:
    if max_stops == 0:
        return 2
    if max_stops == 1:
        return 3
    return 1


_DURATION_RE = re.compile(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", re.IGNORECASE)


def _duration_to_min(v) -> int:
    """SerpAPI는 보통 분 정수로 줌. 문자열이면 'XhYm' 파싱."""
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        m = _DURATION_RE.match(v.strip())
        if m:
            h = int(m.group(1) or 0)
            mn = int(m.group(2) or 0)
            return h * 60 + mn
    return 0


def _parse_dt(s: str) -> datetime:
    """SerpAPI 시각 포맷: 'YYYY-MM-DD HH:MM' (timezone 없음, local time)."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def _build_oneway_flight(item: Dict) -> Optional[Flight]:
    """SerpAPI Google Flights 결과 1개 → 출발-도착을 1편으로 표현 (경유 시 첫·마지막 segment)."""
    legs = item.get("flights", [])
    if not legs:
        return None
    first, last = legs[0], legs[-1]
    try:
        dep_dt = _parse_dt(first["departure_airport"]["time"])
        arr_dt = _parse_dt(last["arrival_airport"]["time"])
    except (KeyError, ValueError):
        return None

    total_dur = _duration_to_min(item.get("total_duration") or 0)
    if total_dur == 0:
        total_dur = max(1, int((arr_dt - dep_dt).total_seconds() // 60))

    return Flight(
        carrier=first.get("airline", "")[:2].upper() or "??",
        flight_no=first.get("flight_number") or "?",
        depart_airport=first["departure_airport"].get("id", ""),
        arrive_airport=last["arrival_airport"].get("id", ""),
        depart_time=dep_dt,
        arrive_time=arr_dt,
        stops=max(0, len(legs) - 1),
        duration_min=total_dur,
        price_krw_per_pax=int(item.get("price") or 0),
    )


class SerpApiFlightAdapter(FlightAdapter):
    """편도 단일 leg 검색. 옵티마이저가 outbound × inbound 조합을 만듦.

    호출수: leg 당 1회. cache.py 적용 시 같은 일자 재실행은 0회.
    """

    def __init__(self, top_k_per_leg: int = 8, cache_ttl_hours: int = 12):
        self.api_key = _get_key()
        self.top_k_per_leg = top_k_per_leg
        self.cache = Cache(path="cache.db", ttl_hours=cache_ttl_hours)

    def search_oneway(self, origin, destination, when, pax, max_stops):
        params = {
            "engine": "google_flights",
            "api_key": self.api_key,
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": when.isoformat(),
            "type": 2,                 # one-way
            "adults": pax,
            "currency": "KRW",
            "hl": "ko",
            "gl": "kr",
            "stops": _stops_param(max_stops),
            "travel_class": 1,         # economy
        }
        data = _api_get(params, cache=self.cache)
        flights: List[Flight] = []
        for item in (data.get("best_flights") or []) + (data.get("other_flights") or []):
            f = _build_oneway_flight(item)
            if f and f.stops <= max_stops:
                flights.append(f)
        flights.sort(key=lambda f: f.price_krw_per_pax)
        return flights[: self.top_k_per_leg]


# ---- Hotel ---------------------------------------------------------------

# 검색 키워드 → 우리 모델의 district 라벨 매핑
_HK_AREA_HINTS = [
    ("Tsim Sha Tsui", ["TSIM SHA TSUI", "TST"]),
    ("Causeway Bay",  ["CAUSEWAY BAY", "CWB"]),
    ("Central",       ["CENTRAL"]),
    ("Mongkok",       ["MONGKOK", "MONG KOK"]),
    ("Jordan",        ["JORDAN"]),
    ("Wan Chai",      ["WAN CHAI", "WANCHAI"]),
]
_MACAU_AREA_HINTS = [
    ("Cotai",            ["COTAI", "TAIPA"]),
    ("Macau Peninsula",  ["MACAU PENINSULA", "PENINSULA"]),
]


def _infer_district(name: str, address: str, hints) -> str:
    text = f"{name} {address}".upper()
    for label, keys in hints:
        if any(k in text for k in keys):
            return label
    return "Other"


def _hotel_class_to_star(s) -> float:
    """SerpAPI는 'hotel_class' 또는 'extracted_hotel_class'를 줌. 후자가 숫자."""
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str):
        m = re.search(r"(\d(?:\.\d)?)", s)
        if m:
            return float(m.group(1))
    return 3.0


class SerpApiHotelAdapter(HotelAdapter):
    """Google Hotels 검색. 가격은 (rooms × nights) 합산을 1박/객실로 환산.

    호출수: 검색 1건당 1회.
    """

    def __init__(self, top_k: int = 30, cache_ttl_hours: int = 12):
        self.api_key = _get_key()
        self.top_k = top_k
        self.cache = Cache(path="cache.db", ttl_hours=cache_ttl_hours)

    def search(self, city, checkin, checkout, rooms, pax):
        nights = max(1, (checkout - checkin).days)
        adults_per_room = max(1, pax // max(1, rooms))

        params = {
            "engine": "google_hotels",
            "api_key": self.api_key,
            "q": f"{city} hotels",
            "check_in_date": checkin.isoformat(),
            "check_out_date": checkout.isoformat(),
            "adults": adults_per_room * rooms,
            "currency": "KRW",
            "hl": "ko",
            "gl": "kr",
            "rooms": rooms,
        }
        data = _api_get(params, cache=self.cache)
        props = data.get("properties") or []

        hints = _MACAU_AREA_HINTS if "macau" in city.lower() else _HK_AREA_HINTS

        results: List[Hotel] = []
        for p in props[: self.top_k]:
            name = p.get("name") or "Unknown"
            star = _hotel_class_to_star(p.get("extracted_hotel_class") or p.get("hotel_class"))
            score = float(p.get("overall_rating") or 0)
            # Google는 0~5 스케일을 자주 씀. 8.0 미만이면 ×2 (10점 환산).
            if 0 < score <= 5:
                score = round(score * 2.0, 1)

            # 가격: rate_per_night.extracted_lowest > total_rate.extracted_lowest
            per_night = 0
            rpn = p.get("rate_per_night") or {}
            tot = p.get("total_rate") or {}
            if rpn.get("extracted_lowest"):
                per_night = int(rpn["extracted_lowest"])
            elif tot.get("extracted_lowest"):
                per_night = int(int(tot["extracted_lowest"]) // max(rooms * nights, 1))
            else:
                continue  # 가격 없는 결과는 스킵

            address = p.get("address") or ""
            district = _infer_district(name, address, hints)

            # MTR/지하철 거리 정보 없음 — 기본값.
            results.append(Hotel(
                name=name,
                star=star,
                review_score=score or 8.0,
                district=district,
                distance_to_mtr_m=300,
                price_per_room_per_night_krw=per_night,
                refundable=bool(p.get("free_cancellation")),
            ))

        results.sort(key=lambda h: h.price_per_room_per_night_krw)
        return results
