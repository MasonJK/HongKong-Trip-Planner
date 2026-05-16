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
  - 같은 요청은 SQLite 캐시(12h TTL)로 재사용 → 무료 한도(250/월) 보호.
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

def _load_env_file(path: str) -> None:
    """KEY=VALUE 형식 파일을 os.environ에 주입.

    파일에 `=`가 하나도 없으면 파일명에서 키 이름을 추론(예: serp_api.env → SERPAPI_KEY)
    하고 파일 전체 내용을 그 값으로 간주. 사용자가 키만 덜렁 붙여넣은 경우 대응.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()

    has_equals = any(("=" in ln and not ln.strip().startswith("#")) for ln in text.splitlines())
    if has_equals:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    else:
        # 파일명 → 환경변수 키 (serp_api.env → SERPAPI_KEY, 그 외 X.env → X_KEY)
        base = os.path.splitext(os.path.basename(path))[0]
        if base.lower() in ("serp_api", "serpapi"):
            env_key = "SERPAPI_KEY"
        else:
            env_key = base.upper().replace("-", "_") + "_KEY"
        value = text.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(env_key, value)


def _get_key() -> str:
    """SERPAPI_KEY를 환경변수 또는 표준 위치의 .env 파일에서 로드.

    탐색 순서:
      1. 이미 설정된 환경변수 SERPAPI_KEY
      2. ./.env
      3. ./env/serp_api.env
      4. ./env/*.env
    """
    if os.environ.get("SERPAPI_KEY"):
        return os.environ["SERPAPI_KEY"]

    candidates = [".env", os.path.join("env", "serp_api.env")]
    if os.path.isdir("env"):
        for name in sorted(os.listdir("env")):
            if name.endswith(".env"):
                p = os.path.join("env", name)
                if p not in candidates:
                    candidates.append(p)

    for path in candidates:
        if os.path.isfile(path):
            _load_env_file(path)
            if os.environ.get("SERPAPI_KEY"):
                return os.environ["SERPAPI_KEY"]

    raise RuntimeError(
        "SERPAPI_KEY를 찾지 못했습니다. .env 또는 env/serp_api.env에 "
        "'SERPAPI_KEY=...' 줄을 두거나 환경변수로 직접 설정하세요."
    )


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


def _build_oneway_flight(item: Dict, pax: int) -> Optional[Flight]:
    """SerpAPI Google Flights 결과 1개 → Flight.

    경유 시: 출발=첫 segment, 도착=마지막 segment, 중간 정보는 stops 카운트로만 보존.
    SerpAPI는 일부 항공편(LCC·코드셰어 등)에 가격을 안 줌 — 이 경우 None 반환해서 후보에서 제외.
    """
    legs = item.get("flights", [])
    if not legs:
        return None

    raw_price = item.get("price")
    if not raw_price:   # None, 0, 빈 문자열 모두 제외
        return None
    per_pax = int(raw_price) // max(pax, 1)

    first, last = legs[0], legs[-1]
    try:
        dep_dt = _parse_dt(first["departure_airport"]["time"])
        arr_dt = _parse_dt(last["arrival_airport"]["time"])
    except (KeyError, ValueError):
        return None

    total_dur = _duration_to_min(item.get("total_duration") or 0)
    if total_dur == 0:
        total_dur = max(1, int((arr_dt - dep_dt).total_seconds() // 60))

    # IATA carrier 코드는 flight_number의 prefix에 있음 ('KE 2005' → 'KE').
    fn = (first.get("flight_number") or "").strip()
    iata = fn.split()[0].upper() if fn else "??"

    return Flight(
        carrier=iata,
        flight_no=fn or "?",
        depart_airport=first["departure_airport"].get("id", ""),
        arrive_airport=last["arrival_airport"].get("id", ""),
        depart_time=dep_dt,
        arrive_time=arr_dt,
        stops=max(0, len(legs) - 1),
        duration_min=total_dur,
        price_krw_per_pax=per_pax,
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
            f = _build_oneway_flight(item, pax)
            if f and f.stops <= max_stops:
                flights.append(f)
        flights.sort(key=lambda f: f.price_krw_per_pax)
        return flights[: self.top_k_per_leg]


# ---- Hotel ---------------------------------------------------------------

# District 중심 좌표 (HK 주요 지역 + 마카오)
_HK_DISTRICT_CENTERS = {
    "Tsim Sha Tsui": (22.2987, 114.1722),
    "Causeway Bay":  (22.2803, 114.1830),
    "Central":       (22.2820, 114.1582),
    "Mongkok":       (22.3193, 114.1694),
    "Jordan":        (22.3049, 114.1722),
    "Wan Chai":      (22.2774, 114.1716),
}
_MACAU_DISTRICT_CENTERS = {
    "Cotai":           (22.1458, 113.5600),
    "Macau Peninsula": (22.1965, 113.5414),
}

# 이름 기반 fallback (좌표가 없거나 모든 중심에서 멀 때)
_HK_AREA_HINTS = [
    ("Tsim Sha Tsui", ["TSIM SHA TSUI", "TST", "尖沙咀"]),
    ("Causeway Bay",  ["CAUSEWAY BAY", "CWB", "銅鑼灣"]),
    ("Central",       ["CENTRAL", "中環"]),
    ("Mongkok",       ["MONGKOK", "MONG KOK", "旺角"]),
    ("Jordan",        ["JORDAN", "佐敦"]),
    ("Wan Chai",      ["WAN CHAI", "WANCHAI", "灣仔"]),
]
_MACAU_AREA_HINTS = [
    ("Cotai",            ["COTAI", "TAIPA", "路氹"]),
    ("Macau Peninsula",  ["MACAU PENINSULA", "PENINSULA"]),
]


def _district_from_gps(lat: float, lon: float, centers: Dict[str, Tuple[float, float]],
                       max_km: float = 2.0) -> Optional[str]:
    """가장 가까운 중심을 찾아 max_km 이내면 그 이름 반환, 아니면 None.
    홍콩 스케일에서는 평면 근사로 충분 (위도 22°에서 cos(lat) ≈ 0.927).
    """
    import math
    best_name, best_d = None, float("inf")
    cos_lat = math.cos(math.radians(lat))
    for name, (clat, clon) in centers.items():
        dx = (lon - clon) * cos_lat * 111.0   # km
        dy = (lat - clat) * 111.0
        d = math.hypot(dx, dy)
        if d < best_d:
            best_name, best_d = name, d
    return best_name if best_d <= max_km else None


def _infer_district(name: str, address: str, hints) -> Optional[str]:
    text = f"{name} {address}".upper()
    for label, keys in hints:
        if any(k.upper() in text for k in keys):
            return label
    return None


def _resolve_district(prop: Dict, is_macau: bool) -> str:
    centers = _MACAU_DISTRICT_CENTERS if is_macau else _HK_DISTRICT_CENTERS
    hints = _MACAU_AREA_HINTS if is_macau else _HK_AREA_HINTS

    gps = prop.get("gps_coordinates") or {}
    lat = gps.get("latitude")
    lon = gps.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        d = _district_from_gps(float(lat), float(lon), centers)
        if d:
            return d

    # GPS가 없거나 중심에서 멀면 이름 기반 fallback
    name = prop.get("name") or ""
    addr = prop.get("address") or ""
    d = _infer_district(name, addr, hints)
    return d or "Other"


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
        is_macau = "macau" in city.lower()

        results: List[Hotel] = []
        for p in props[: self.top_k]:
            name = p.get("name") or "Unknown"
            star = _hotel_class_to_star(p.get("extracted_hotel_class") or p.get("hotel_class"))
            score = float(p.get("overall_rating") or 0)
            # Google는 0~5 스케일을 자주 씀. 5.0 이하면 ×2 (10점 환산).
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

            district = _resolve_district(p, is_macau)

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
