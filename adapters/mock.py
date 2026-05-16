"""모의 데이터 어댑터.

실제 ICN-HKG/MFM 항공권·홍콩/마카오 호텔 가격대를 반영한 의사 난수 데이터를 생성합니다.
운영 전환 시 SerpAPI 어댑터 등으로 교체.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from typing import List

from ..models import Flight, Hotel
from .base import FlightAdapter, HotelAdapter


# 항공사 시드 (코드, 회사명, 직항여부, 편도 기본가, 슬롯 후보)
_CARRIERS = [
    ("KE", "대한항공",       True, 260000, [(9, 25), (14, 30), (19, 50)]),
    ("OZ", "아시아나항공",   True, 255000, [(8, 55), (13, 40), (20, 10)]),
    ("CX", "캐세이퍼시픽",   True, 270000, [(10, 0), (16, 20), (21, 5)]),
    ("HX", "홍콩항공",       True, 230000, [(11, 15), (15, 45), (22, 30)]),
    ("LJ", "진에어",         True, 190000, [(7, 30), (13, 0), (18, 20)]),
    ("7C", "제주항공",       True, 195000, [(6, 50), (12, 15), (19, 0)]),
    ("UO", "홍콩익스프레스", True, 175000, [(8, 0), (14, 50), (23, 40)]),
    ("MU", "중국동방항공",   False, 160000, [(7, 20), (14, 0)]),  # 상하이 경유
]

# 홍콩 호텔 시드 (이름, 별, 평점, 구역, MTR거리m, 1박/객실 KRW, 환불가능)
_HK_HOTELS = [
    ("Hotel ICON",                4.5, 8.7, "Tsim Sha Tsui", 350,  298000, True),
    ("The Salisbury YMCA",         3.0, 8.4, "Tsim Sha Tsui", 200,  165000, True),
    ("Eaton HK",                   4.0, 8.5, "Jordan",        150,  215000, True),
    ("Cordis Hong Kong",           5.0, 8.9, "Mongkok",       100,  385000, True),
    ("Mira Hotel",                 5.0, 8.8, "Tsim Sha Tsui", 250,  340000, False),
    ("Dorsett Mongkok",            4.0, 8.2, "Mongkok",       180,  175000, True),
    ("ibis Hong Kong Central",     3.0, 8.1, "Central",       300,  189000, True),
    ("The Pottinger",              5.0, 9.0, "Central",       120,  420000, False),
    ("Mini Hotel Causeway Bay",    3.0, 8.0, "Causeway Bay",  220,  145000, True),
    ("Park Lane Causeway Bay",     4.5, 8.6, "Causeway Bay",   90,  295000, True),
    ("Butterfly on Wellington",    4.0, 8.3, "Central",       150,  225000, True),
    ("BP International",           4.0, 8.0, "Jordan",        100,  195000, True),
]

# 마카오 호텔 시드 (이름, 별, 평점, 구역, 도보거리m, 1박/객실 KRW, 환불가능)
_MACAU_HOTELS = [
    ("The Venetian Macao",          5.0, 8.7, "Cotai",            300,  295000, True),
    ("Galaxy Hotel",                5.0, 8.8, "Cotai",            250,  340000, True),
    ("Sofitel Macau At Ponte 16",   5.0, 8.5, "Macau Peninsula",  150,  225000, True),
    ("Grand Lisboa Palace",         5.0, 8.6, "Cotai",            200,  370000, True),
    ("Hotel Royal Macau",           4.0, 8.0, "Macau Peninsula",  300,  155000, True),
    ("Holiday Inn Macao Cotai",     4.0, 8.2, "Cotai",            200,  185000, True),
    ("Sheraton Grand Macao",        5.0, 8.5, "Cotai",            220,  265000, True),
    ("Studio City Macau",           5.0, 8.6, "Cotai",            350,  240000, True),
]


def _make_flight(carrier_idx: int, dep_airport: str, arr_airport: str,
                 day: date, slot_idx: int, price_variance: float) -> Flight:
    code, _, direct, base_price, slots = _CARRIERS[carrier_idx]
    h, m = slots[slot_idx % len(slots)]
    dep_dt = datetime.combine(day, time(h, m))
    # ICN ↔ MFM은 직항이 거의 없어 약간 더 길게
    base_dur = 215 if direct else 380
    if "MFM" in (dep_airport, arr_airport):
        base_dur = base_dur + 25
    arr_dt = dep_dt + timedelta(minutes=base_dur)
    price = int(base_price * price_variance)
    return Flight(
        carrier=code,
        flight_no=f"{code}{600 + carrier_idx*10 + slot_idx}",
        depart_airport=dep_airport,
        arrive_airport=arr_airport,
        depart_time=dep_dt,
        arrive_time=arr_dt,
        stops=0 if direct else 1,
        duration_min=base_dur,
        price_krw_per_pax=price,
    )


class MockFlightAdapter(FlightAdapter):
    """결정적(seed 고정) 모의 데이터. 같은 (when, origin, destination) → 항상 같은 결과."""

    def __init__(self, seed_base: int = 42):
        self.seed_base = seed_base

    def search_oneway(self, origin, destination, when, pax, max_stops):
        seed = self.seed_base + when.toordinal() + (hash(origin) ^ hash(destination)) % 1000
        rng = random.Random(seed)
        results: List[Flight] = []

        # 성수기 가격 조정
        month_factor = 1.15 if when.month == 10 and when.day <= 8 else 1.0

        # MFM은 직항 옵션이 거의 없으니 가격을 좀 더 올림
        mfm_factor = 1.10 if "MFM" in (origin, destination) else 1.0

        for c_idx, (_, _, direct, _, slots) in enumerate(_CARRIERS):
            if not direct and max_stops < 1:
                continue
            for s in range(min(2, len(slots))):
                variance = rng.uniform(0.85, 1.20) * month_factor * mfm_factor
                results.append(_make_flight(c_idx, origin, destination, when, s, variance))

        results.sort(key=lambda f: f.price_krw_per_pax)
        return results


class MockHotelAdapter(HotelAdapter):
    def __init__(self, seed_base: int = 42):
        self.seed_base = seed_base

    def search(self, city, checkin, checkout, rooms, pax):
        rng = random.Random(self.seed_base + checkin.toordinal() + hash(city) % 1000)
        results: List[Hotel] = []

        month_factor = 1.20 if checkin.month == 10 and checkin.day <= 8 else 1.0
        seed = _MACAU_HOTELS if "macau" in city.lower() else _HK_HOTELS

        for name, star, score, district, mtr, base_price, refundable in seed:
            variance = rng.uniform(0.90, 1.15) * month_factor
            price = int(base_price * variance)
            results.append(Hotel(
                name=name,
                star=star,
                review_score=round(score + rng.uniform(-0.1, 0.1), 1),
                district=district,
                distance_to_mtr_m=mtr,
                price_per_room_per_night_krw=price,
                refundable=refundable,
            ))

        results.sort(key=lambda h: h.price_per_room_per_night_krw)
        return results
