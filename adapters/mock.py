"""모의 데이터 어댑터.

실제 ICN-HKG 항공권/홍콩 호텔 가격대를 반영한 의사 난수 데이터를 생성합니다.
운영 전환 시 PlaywrightFlightAdapter 등으로 교체하기만 하면 됩니다.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from typing import List

from ..models import Flight, FlightItinerary, Hotel
from .base import FlightAdapter, HotelAdapter


# 실제 항공사·운항 패턴 기반 시드 데이터
_CARRIERS = [
    # (코드, 회사명, 직항여부, 기본가, 시간슬롯 후보 - 출발지 기준)
    ("KE", "대한항공", True, 520000, [(9, 25), (14, 30), (19, 50)]),
    ("OZ", "아시아나항공", True, 510000, [(8, 55), (13, 40), (20, 10)]),
    ("CX", "캐세이퍼시픽", True, 540000, [(10, 0), (16, 20), (21, 5)]),
    ("HX", "홍콩항공", True, 460000, [(11, 15), (15, 45), (22, 30)]),
    ("LJ", "진에어", True, 380000, [(7, 30), (13, 0), (18, 20)]),
    ("7C", "제주항공", True, 390000, [(6, 50), (12, 15), (19, 0)]),
    ("UO", "홍콩익스프레스", True, 350000, [(8, 0), (14, 50), (23, 40)]),  # 야간편 일부러 포함
    ("MU", "중국동방항공", False, 320000, [(7, 20), (14, 0)]),  # 상하이 경유
]

# 홍콩 호텔 시드 데이터 (실제 호텔명·구역 반영, 가격은 평균값 근사)
_HOTELS = [
    # (이름, 별, 평점, 구역, MTR거리m, 1박/객실 KRW, 환불가능)
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


def _make_flight(carrier_idx: int, dep_airport: str, arr_airport: str,
                 day: date, slot_idx: int, price_variance: float) -> Flight:
    code, name, direct, base_price, slots = _CARRIERS[carrier_idx]
    h, m = slots[slot_idx % len(slots)]
    dep_dt = datetime.combine(day, time(h, m))
    duration_min = 215 if direct else 380  # 직항 3h35, 경유 6h20
    arr_dt = dep_dt + timedelta(minutes=duration_min)
    price = int(base_price * price_variance)
    return Flight(
        carrier=code,
        flight_no=f"{code}{600 + carrier_idx*10 + slot_idx}",
        depart_airport=dep_airport,
        arrive_airport=arr_airport,
        depart_time=dep_dt,
        arrive_time=arr_dt,
        stops=0 if direct else 1,
        duration_min=duration_min,
        price_krw_per_pax=price,
    )


class MockFlightAdapter(FlightAdapter):
    """결정적(seed 고정) 모의 데이터. 같은 날짜 → 항상 같은 결과."""

    def __init__(self, seed_base: int = 42):
        self.seed_base = seed_base

    def search(self, origin, destination, depart, return_, pax, max_stops):
        # 일자별로 안정적인 난수 시드
        rng = random.Random(self.seed_base + depart.toordinal())
        results: List[FlightItinerary] = []

        # 성수기·국경절 등 가격 조정 계수
        month_factor = 1.15 if depart.month == 10 and depart.day <= 8 else 1.0
        weekend_factor = 1.05  # 일요일 출발 약간 비쌈

        for c_idx, (code, _, direct, _, slots) in enumerate(_CARRIERS):
            if not direct and max_stops < 1:
                continue
            # 각 항공사별로 슬롯 2개 정도 샘플링
            for s in range(min(2, len(slots))):
                variance = rng.uniform(0.85, 1.20) * month_factor * weekend_factor
                outbound = _make_flight(c_idx, origin, destination, depart, s, variance)

                # 귀국편: 슬롯 다양화
                r_var = rng.uniform(0.85, 1.20) * month_factor
                inbound = _make_flight(c_idx, destination, origin, return_, (s + 1) % len(slots), r_var)

                results.append(FlightItinerary(outbound=outbound, inbound=inbound, pax=pax))

        # 가격 오름차순
        results.sort(key=lambda it: it.total_price_krw)
        return results


class MockHotelAdapter(HotelAdapter):
    def __init__(self, seed_base: int = 42):
        self.seed_base = seed_base

    def search(self, city, checkin, checkout, rooms, pax):
        rng = random.Random(self.seed_base + checkin.toordinal())
        nights = (checkout - checkin).days
        results: List[Hotel] = []

        month_factor = 1.20 if checkin.month == 10 and checkin.day <= 8 else 1.0

        for name, star, score, district, mtr, base_price, refundable in _HOTELS:
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
