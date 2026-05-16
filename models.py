"""여행 후보를 표현하는 데이터 클래스."""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Optional


@dataclass
class Flight:
    carrier: str
    flight_no: str
    depart_airport: str
    arrive_airport: str
    depart_time: datetime
    arrive_time: datetime
    stops: int
    duration_min: int
    price_krw_per_pax: int   # 편도가/인


@dataclass
class FlightItinerary:
    outbound: Flight
    inbound: Flight
    pax: int

    @property
    def total_price_krw(self) -> int:
        return (self.outbound.price_krw_per_pax + self.inbound.price_krw_per_pax) * self.pax

    @property
    def total_flight_hours(self) -> float:
        return (self.outbound.duration_min + self.inbound.duration_min) / 60.0


@dataclass
class Hotel:
    name: str
    star: float
    review_score: float          # 0~10
    district: str
    distance_to_mtr_m: int
    price_per_room_per_night_krw: int
    refundable: bool


@dataclass
class TripCandidate:
    """한 가지 시나리오의 여행 옵션."""

    arrival: date
    return_date: date
    nights: int           # 전체 박수
    pax: int
    rooms: int
    flight: FlightItinerary

    # HK 숙소 (모든 시나리오 공통)
    hk_hotel: Hotel
    hk_nights: int

    # 마카오 1박 옵션 (있을 때만)
    macau_hotel: Optional[Hotel] = None
    macau_nights: int = 0

    # 부가 비용 (페리·입국세 등) — 시나리오별로 계산
    side_trip_krw_per_pax: int = 0

    fx_buffer_pct: float = 0.0
    scenario: str = "day_trip"   # day_trip | overnight_rt | overnight_mfm_out

    score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    @property
    def hk_hotel_cost_krw(self) -> int:
        return self.hk_hotel.price_per_room_per_night_krw * self.hk_nights * self.rooms

    @property
    def macau_hotel_cost_krw(self) -> int:
        if not self.macau_hotel:
            return 0
        return self.macau_hotel.price_per_room_per_night_krw * self.macau_nights * self.rooms

    @property
    def hotel_cost_krw(self) -> int:
        return self.hk_hotel_cost_krw + self.macau_hotel_cost_krw

    @property
    def subtotal_krw(self) -> int:
        return (
            self.flight.total_price_krw
            + self.hotel_cost_krw
            + self.side_trip_krw_per_pax * self.pax
        )

    @property
    def total_cost_krw(self) -> int:
        return int(self.subtotal_krw * (1 + self.fx_buffer_pct / 100))

    @property
    def cost_per_person_krw(self) -> int:
        return self.total_cost_krw // self.pax

    @property
    def avg_review_score(self) -> float:
        """박수 가중 평균 (마카오 1박이면 호텔 점수도 박수 비례 평균)."""
        total = self.hk_nights + self.macau_nights
        if total == 0:
            return 0.0
        score = self.hk_hotel.review_score * self.hk_nights
        if self.macau_hotel:
            score += self.macau_hotel.review_score * self.macau_nights
        return score / total
