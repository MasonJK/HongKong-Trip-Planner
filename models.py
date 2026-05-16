"""여행 후보를 표현하는 데이터 클래스."""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List


@dataclass
class Flight:
    carrier: str            # 항공사 코드 (KE, OZ, CX, HX...)
    flight_no: str
    depart_airport: str
    arrive_airport: str
    depart_time: datetime
    arrive_time: datetime
    stops: int
    duration_min: int
    price_krw_per_pax: int  # 1인 왕복가가 아니라 1인 편도가


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
    distance_to_mtr_m: int       # MTR역까지 도보 거리(m)
    price_per_room_per_night_krw: int
    refundable: bool


@dataclass
class TripCandidate:
    sunday: date
    return_date: date
    nights: int
    pax: int
    flight: FlightItinerary
    hotel: Hotel
    rooms: int
    side_trip_cost_krw: int
    fx_buffer_pct: float
    score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    @property
    def hotel_cost_krw(self) -> int:
        return self.hotel.price_per_room_per_night_krw * self.nights * self.rooms

    @property
    def subtotal_krw(self) -> int:
        return (
            self.flight.total_price_krw
            + self.hotel_cost_krw
            + self.side_trip_cost_krw * self.pax
        )

    @property
    def total_cost_krw(self) -> int:
        return int(self.subtotal_krw * (1 + self.fx_buffer_pct / 100))

    @property
    def cost_per_person_krw(self) -> int:
        return self.total_cost_krw // self.pax
