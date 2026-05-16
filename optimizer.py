"""후보 일자 생성 → 검색 → 필터링 → 조합 → 점수 → 랭킹."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional

from .adapters.base import FlightAdapter, HotelAdapter
from .models import FlightItinerary, Hotel, TripCandidate


_WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def generate_candidate_dates(
    start: date, end: date, weekday,
    exclude_ranges: Optional[List[Dict]] = None,
) -> List[date]:
    if isinstance(weekday, str):
        weekday = [weekday]
    wds = {_WEEKDAY_MAP[w.lower()] for w in weekday}
    excludes = exclude_ranges or []
    days: List[date] = []
    d = start
    while d <= end:
        if d.weekday() in wds:
            blocked = any(ex["start"] <= d <= ex["end"] for ex in excludes)
            if not blocked:
                days.append(d)
        d += timedelta(days=1)
    return days


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def flight_within_window(itin: FlightItinerary, earliest: time, latest: time) -> bool:
    """출발편·귀국편 모두 출발/도착 시각이 허용 범위 안인지."""
    for f in (itin.outbound, itin.inbound):
        if f.depart_time.time() < earliest or f.depart_time.time() > latest:
            return False
        if f.arrive_time.time() < earliest or f.arrive_time.time() > latest:
            return False
    return True


def _norm(v: float, lo: float, hi: float, invert: bool = False) -> float:
    if hi <= lo:
        return 0.5
    x = (v - lo) / (hi - lo)
    x = max(0.0, min(1.0, x))
    return 1.0 - x if invert else x


def score_candidates(candidates: List[TripCandidate], weights: Dict[str, float],
                     preferred_districts: List[str]) -> None:
    if not candidates:
        return
    costs = [c.cost_per_person_krw for c in candidates]
    flight_hours = [c.flight.total_flight_hours for c in candidates]
    hotel_scores = [c.hotel.review_score for c in candidates]
    mtr_dists = [c.hotel.distance_to_mtr_m for c in candidates]

    c_lo, c_hi = min(costs), max(costs)
    f_lo, f_hi = min(flight_hours), max(flight_hours)
    h_lo, h_hi = min(hotel_scores), max(hotel_scores)
    m_lo, m_hi = min(mtr_dists), max(mtr_dists)

    for c in candidates:
        s_cost = _norm(c.cost_per_person_krw, c_lo, c_hi, invert=True)  # 쌀수록 좋음
        s_ft = _norm(c.flight.total_flight_hours, f_lo, f_hi, invert=True)  # 짧을수록 좋음
        s_hs = _norm(c.hotel.review_score, h_lo, h_hi)  # 높을수록 좋음
        s_loc_district = 1.0 if c.hotel.district in preferred_districts else 0.3
        s_loc_mtr = _norm(c.hotel.distance_to_mtr_m, m_lo, m_hi, invert=True)
        s_loc = (s_loc_district + s_loc_mtr) / 2
        s_flex = 1.0 if c.hotel.refundable else 0.0

        score = (
            weights.get("cost", 0) * s_cost
            + weights.get("flight_time", 0) * s_ft
            + weights.get("hotel_score", 0) * s_hs
            + weights.get("location", 0) * s_loc
            + weights.get("cancel_flex", 0) * s_flex
        )
        c.score = round(score, 4)
        c.score_breakdown = {
            "cost": round(s_cost, 3),
            "flight_time": round(s_ft, 3),
            "hotel_score": round(s_hs, 3),
            "location": round(s_loc, 3),
            "cancel_flex": round(s_flex, 3),
        }


def optimize(config: Dict, flight_adapter: FlightAdapter, hotel_adapter: HotelAdapter) -> List[TripCandidate]:
    trip = config["trip"]
    fly_cfg = config["flight"]
    hotel_cfg = config["hotel"]
    rank_cfg = config["ranking"]

    origin = trip["origin"]
    destination = trip["destination"]
    nights = trip["nights"]
    pax = trip["travelers"]["adults"]
    budget = trip["budget_per_person_krw"]
    rooms = hotel_cfg["rooms"]

    side_per_pax = sum(s.get("cost_per_person_krw", 0) for s in config.get("side_trips", []))
    fx_buffer = config.get("fx_buffer_pct", 0)

    earliest = parse_hhmm(fly_cfg["earliest_departure"])
    latest = parse_hhmm(fly_cfg["latest_arrival"])

    arrival_dates = generate_candidate_dates(
        date.fromisoformat(str(trip["date_range"]["start"])),
        date.fromisoformat(str(trip["date_range"]["end"])),
        trip["arrival_day_of_week"],
        config.get("exclude_date_ranges", []),
    )

    candidates: List[TripCandidate] = []
    for arrival in arrival_dates:
        return_ = arrival + timedelta(days=nights)

        flights = flight_adapter.search(
            origin, destination, arrival, return_, pax, fly_cfg["max_stops"]
        )
        flights = [f for f in flights if flight_within_window(f, earliest, latest)]
        flights = flights[: fly_cfg.get("top_k_per_date", 5)]

        hotels = hotel_adapter.search("Hong Kong", arrival, return_, rooms, pax)
        hotels = [h for h in hotels if h.review_score >= hotel_cfg["min_review_score"]]
        hotels = hotels[: hotel_cfg.get("top_k_per_date", 5)]

        for f in flights:
            for h in hotels:
                cand = TripCandidate(
                    arrival=arrival,
                    return_date=return_,
                    nights=nights,
                    pax=pax,
                    flight=f,
                    hotel=h,
                    rooms=rooms,
                    side_trip_cost_krw=side_per_pax,
                    fx_buffer_pct=fx_buffer,
                )
                if cand.cost_per_person_krw > budget:
                    continue
                candidates.append(cand)

    score_candidates(candidates, rank_cfg["weights"], hotel_cfg["preferred_districts"])
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[: rank_cfg.get("top_n", 10)]
