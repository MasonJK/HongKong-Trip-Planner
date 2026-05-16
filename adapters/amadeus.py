"""Amadeus Self-Service API 어댑터.

사용 전 준비:
  1. https://developers.amadeus.com 에서 가입 (무료, 카드 불필요)
  2. My Self-Service Workspace → Create New App → API Key/Secret 발급
  3. 환경변수로 주입:
       export AMADEUS_API_KEY=xxx
       export AMADEUS_API_SECRET=xxx
       export AMADEUS_ENV=test       # test | production

테스트 환경(test)은 무료지만 일부 노선/날짜 데이터가 비어있을 수 있습니다.
프로덕션(production)도 월 2000건 무료지만 신청 후 며칠 승인 대기가 있습니다.
"""
from __future__ import annotations

import json
import os
import time as _time
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from ..fx import to_krw
from ..models import Flight, FlightItinerary, Hotel
from .base import FlightAdapter, HotelAdapter


# 도시→Amadeus city code (필요 시 확장)
_CITY_CODE = {"Hong Kong": "HKG", "Macau": "MFM", "Seoul": "SEL", "Tokyo": "TYO"}


class _AmadeusClient:
    """OAuth2 토큰 관리 + GET 요청 래퍼."""

    def __init__(self, key: Optional[str] = None, secret: Optional[str] = None, env: Optional[str] = None):
        self.key = key or os.environ.get("AMADEUS_API_KEY")
        self.secret = secret or os.environ.get("AMADEUS_API_SECRET")
        self.env = (env or os.environ.get("AMADEUS_ENV") or "test").lower()
        if not self.key or not self.secret:
            raise RuntimeError(
                "AMADEUS_API_KEY / AMADEUS_API_SECRET 환경변수가 설정되지 않았습니다."
            )
        self.base = (
            "https://api.amadeus.com" if self.env == "production"
            else "https://test.api.amadeus.com"
        )
        self._token: Optional[str] = None
        self._token_exp: float = 0

    def _ensure_token(self):
        if self._token and _time.time() < self._token_exp - 30:
            return
        body = urlencode({
            "grant_type": "client_credentials",
            "client_id": self.key,
            "client_secret": self.secret,
        }).encode()
        req = Request(
            f"{self.base}/v1/security/oauth2/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        self._token = data["access_token"]
        self._token_exp = _time.time() + data.get("expires_in", 1700)

    def get(self, path: str, params: Dict) -> Dict:
        self._ensure_token()
        url = f"{self.base}{path}?{urlencode(params)}"
        req = Request(url, headers={"Authorization": f"Bearer {self._token}"})
        try:
            with urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except HTTPError as e:
            try:
                body = e.read().decode()
            except Exception:
                body = ""
            raise RuntimeError(f"Amadeus {path} HTTP {e.code}: {body[:300]}") from e


def _parse_iso(s: str) -> datetime:
    # Amadeus는 timezone 정보 없는 local time을 줌. 그대로 사용.
    return datetime.fromisoformat(s)


def _itinerary_summary(itin_json: Dict) -> Optional[Flight]:
    """편명·시각·시간을 첫 segment 기준으로 단순화.
    경유가 있어도 표시는 출발→최종도착으로 묶음.
    """
    segments = itin_json.get("segments", [])
    if not segments:
        return None
    first, last = segments[0], segments[-1]
    dep = first["departure"]
    arr = last["arrival"]

    duration_iso = itin_json.get("duration", "PT0M")  # ex: PT3H35M
    h, m = 0, 0
    cur = ""
    for ch in duration_iso.replace("PT", ""):
        if ch.isdigit():
            cur += ch
        else:
            v = int(cur) if cur else 0
            if ch == "H":
                h = v
            elif ch == "M":
                m = v
            cur = ""
    duration_min = h * 60 + m

    return Flight(
        carrier=first["carrierCode"],
        flight_no=f"{first['carrierCode']}{first['number']}",
        depart_airport=dep["iataCode"],
        arrive_airport=arr["iataCode"],
        depart_time=_parse_iso(dep["at"]),
        arrive_time=_parse_iso(arr["at"]),
        stops=max(0, len(segments) - 1),
        duration_min=duration_min,
        price_krw_per_pax=0,  # 호출자가 채움 (편도 분배)
    )


class AmadeusFlightAdapter(FlightAdapter):
    def __init__(self, client: Optional[_AmadeusClient] = None, max_offers: int = 20):
        self.client = client or _AmadeusClient()
        self.max_offers = max_offers

    def search(self, origin, destination, depart, return_, pax, max_stops):
        params = {
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": depart.isoformat(),
            "returnDate": return_.isoformat(),
            "adults": pax,
            "currencyCode": "KRW",
            "max": self.max_offers,
            "nonStop": "true" if max_stops == 0 else "false",
            "travelClass": "ECONOMY",
        }
        data = self.client.get("/v2/shopping/flight-offers", params)
        offers = data.get("data", [])

        results: List[FlightItinerary] = []
        for offer in offers:
            itins = offer.get("itineraries", [])
            if len(itins) < 2:
                continue
            outbound = _itinerary_summary(itins[0])
            inbound = _itinerary_summary(itins[1])
            if not outbound or not inbound:
                continue
            if outbound.stops > max_stops or inbound.stops > max_stops:
                continue

            price = offer.get("price", {})
            total_amount = float(price.get("grandTotal") or price.get("total", 0))
            currency = price.get("currency", "KRW")
            total_krw = to_krw(total_amount, currency)
            per_pax_total = total_krw // max(pax, 1)
            # 편도가는 단순 절반 분배 (Amadeus는 왕복 묶음가만 줌)
            half = per_pax_total // 2
            outbound.price_krw_per_pax = half
            inbound.price_krw_per_pax = per_pax_total - half

            results.append(FlightItinerary(outbound=outbound, inbound=inbound, pax=pax))

        results.sort(key=lambda it: it.total_price_krw)
        return results


# 홍콩 주요 지역의 hotel id ↔ 구역 라벨 매핑 (필요시 확장)
_HK_DISTRICT_HINTS = [
    ("Tsim Sha Tsui", ["TST", "TSIM SHA TSUI"]),
    ("Causeway Bay",  ["CAUSEWAY BAY", "CWB"]),
    ("Central",       ["CENTRAL"]),
    ("Mongkok",       ["MONGKOK", "MONG KOK"]),
    ("Jordan",        ["JORDAN"]),
    ("Wan Chai",      ["WAN CHAI", "WANCHAI"]),
]


def _infer_district(name: str, address: str) -> str:
    text = f"{name} {address}".upper()
    for label, keys in _HK_DISTRICT_HINTS:
        if any(k in text for k in keys):
            return label
    return "Other"


class AmadeusHotelAdapter(HotelAdapter):
    def __init__(self, client: Optional[_AmadeusClient] = None, max_hotels: int = 30):
        self.client = client or _AmadeusClient()
        self.max_hotels = max_hotels

    def _list_hotel_ids(self, city_code: str) -> List[Dict]:
        data = self.client.get(
            "/v1/reference-data/locations/hotels/by-city",
            {"cityCode": city_code, "radius": 10, "radiusUnit": "KM"},
        )
        return data.get("data", [])[: self.max_hotels]

    def search(self, city, checkin, checkout, rooms, pax):
        code = _CITY_CODE.get(city) or city.upper()[:3]
        hotels_meta = self._list_hotel_ids(code)
        if not hotels_meta:
            return []

        # 한 번에 너무 많은 ID를 넣으면 414 — 청크로 나눠 호출
        ids = [h["hotelId"] for h in hotels_meta if h.get("hotelId")]
        meta_by_id = {h["hotelId"]: h for h in hotels_meta if h.get("hotelId")}

        nights = (checkout - checkin).days
        adults_per_room = max(1, pax // rooms)

        results: List[Hotel] = []
        CHUNK = 25
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i : i + CHUNK]
            params = {
                "hotelIds": ",".join(chunk),
                "checkInDate": checkin.isoformat(),
                "checkOutDate": checkout.isoformat(),
                "adults": adults_per_room,
                "roomQuantity": rooms,
                "currency": "KRW",
                "bestRateOnly": "true",
            }
            try:
                data = self.client.get("/v3/shopping/hotel-offers", params)
            except RuntimeError:
                continue

            for entry in data.get("data", []):
                hinfo = entry.get("hotel", {})
                offers = entry.get("offers", [])
                if not offers:
                    continue
                offer = offers[0]
                price = offer.get("price", {})
                total = float(price.get("total", 0))
                currency = price.get("currency", "KRW")
                total_krw = to_krw(total, currency)
                # offer.total은 (rooms * nights) 합산가
                per_room_per_night = total_krw // max(rooms * nights, 1)

                meta = meta_by_id.get(hinfo.get("hotelId"), {})
                addr_parts = (hinfo.get("address") or {}).get("lines") or []
                address = " ".join(addr_parts) if isinstance(addr_parts, list) else ""

                rating = meta.get("rating") or hinfo.get("rating")
                star = float(rating) if rating else 3.0

                cancel_policies = offer.get("policies", {}).get("cancellations", [])
                refundable = bool(cancel_policies) and not all(
                    p.get("type") == "FULL_STAY" for p in cancel_policies
                )

                results.append(Hotel(
                    name=hinfo.get("name", "Unknown"),
                    star=star,
                    review_score=8.0,  # Amadeus는 리뷰 점수 미제공 — 기본값
                    district=_infer_district(hinfo.get("name", ""), address),
                    distance_to_mtr_m=300,  # 미제공 — 기본값
                    price_per_room_per_night_krw=per_room_per_night,
                    refundable=refundable,
                ))

        results.sort(key=lambda h: h.price_per_room_per_night_krw)
        return results
