"""후보 일자 생성 → 검색 → 시나리오 enum → 필터 → 조합 → 점수 → 랭킹."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional

from .adapters.base import FlightAdapter, HotelAdapter
from .models import Flight, FlightItinerary, Hotel, TripCandidate


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


def flight_in_window(f: Flight, earliest: time, latest: time,
                     max_duration_min: Optional[int] = None) -> bool:
    if f.depart_time.time() < earliest or f.depart_time.time() > latest:
        return False
    if f.arrive_time.time() < earliest or f.arrive_time.time() > latest:
        return False
    if max_duration_min is not None and f.duration_min > max_duration_min:
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
    hotel_scores = [c.avg_review_score for c in candidates]
    mtr_dists = [c.hk_hotel.distance_to_mtr_m for c in candidates]

    c_lo, c_hi = min(costs), max(costs)
    f_lo, f_hi = min(flight_hours), max(flight_hours)
    h_lo, h_hi = min(hotel_scores), max(hotel_scores)
    m_lo, m_hi = min(mtr_dists), max(mtr_dists)

    for c in candidates:
        s_cost = _norm(c.cost_per_person_krw, c_lo, c_hi, invert=True)
        s_ft = _norm(c.flight.total_flight_hours, f_lo, f_hi, invert=True)
        s_hs = _norm(c.avg_review_score, h_lo, h_hi)
        s_loc_district = 1.0 if c.hk_hotel.district in preferred_districts else 0.3
        s_loc_mtr = _norm(c.hk_hotel.distance_to_mtr_m, m_lo, m_hi, invert=True)
        s_loc = (s_loc_district + s_loc_mtr) / 2
        # 환불 유연성: HK + (있다면) 마카오 모두 환불 가능해야 1.0
        flex_parts = [c.hk_hotel.refundable]
        if c.macau_hotel:
            flex_parts.append(c.macau_hotel.refundable)
        s_flex = 1.0 if all(flex_parts) else 0.0

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


# ---- 항공편 조합 헬퍼 -----------------------------------------------------

def _filter_and_trim(flights: List[Flight], earliest: time, latest: time, top_k: int,
                     max_duration_min: Optional[int] = None) -> List[Flight]:
    flights = [f for f in flights if flight_in_window(f, earliest, latest, max_duration_min)]
    flights = flights[:top_k]
    return flights


def _pair(out_list: List[Flight], in_list: List[Flight], pax: int) -> List[FlightItinerary]:
    pairs: List[FlightItinerary] = []
    for o in out_list:
        for i in in_list:
            pairs.append(FlightItinerary(outbound=o, inbound=i, pax=pax))
    return pairs


# ---- 메인 ----------------------------------------------------------------

def optimize(config: Dict, flight_adapter: FlightAdapter, hotel_adapter: HotelAdapter) -> List[TripCandidate]:
    trip = config["trip"]
    fly_cfg = config["flight"]
    hotel_cfg = config["hotel"]
    rank_cfg = config["ranking"]
    macau_cfg = config.get("macau") or {}

    origin = trip["origin"]
    destination = trip["destination"]    # HKG
    macau_iata = macau_cfg.get("airport", "MFM")
    nights = trip["nights"]
    pax = trip["travelers"]["adults"]
    budget = trip["budget_per_person_krw"]
    rooms = hotel_cfg["rooms"]
    max_stops = fly_cfg["max_stops"]

    fx_buffer = config.get("fx_buffer_pct", 0)
    earliest = parse_hhmm(fly_cfg["earliest_departure"])
    latest = parse_hhmm(fly_cfg["latest_arrival"])
    max_dur = fly_cfg.get("max_duration_min")
    top_k_flight = fly_cfg.get("top_k_per_date", 5)
    top_k_hotel = hotel_cfg.get("top_k_per_date", 5)

    scenarios = macau_cfg.get("scenarios", {}) or {}
    do_day_trip = scenarios.get("day_trip", True)
    do_overnight_rt = scenarios.get("overnight_rt", False)
    do_overnight_mfm_out = scenarios.get("overnight_mfm_out", False)

    ferry_rt = int(macau_cfg.get("ferry_round_trip_krw_per_pax", 50000))
    ferry_ow = int(macau_cfg.get("ferry_one_way_krw_per_pax", 28000))

    macau_hotel_cfg = macau_cfg.get("hotel", {}) or {}
    macau_min_score = float(macau_hotel_cfg.get("min_review_score", 7.5))
    macau_top_k = int(macau_hotel_cfg.get("top_k_per_date", 3))
    macau_overnight_used = do_overnight_rt or do_overnight_mfm_out

    arrival_dates = generate_candidate_dates(
        date.fromisoformat(str(trip["date_range"]["start"])),
        date.fromisoformat(str(trip["date_range"]["end"])),
        trip["arrival_day_of_week"],
        config.get("exclude_date_ranges", []),
    )

    candidates: List[TripCandidate] = []
    for arrival in arrival_dates:
        return_ = arrival + timedelta(days=nights)

        # 항공: HKG↔HKG는 모든 시나리오에서 사용 가능. MFM 귀국은 시나리오 C에서만.
        out_hkg = _filter_and_trim(
            flight_adapter.search_oneway(origin, destination, arrival, pax, max_stops),
            earliest, latest, top_k_flight, max_dur,
        )
        in_hkg = _filter_and_trim(
            flight_adapter.search_oneway(destination, origin, return_, pax, max_stops),
            earliest, latest, top_k_flight, max_dur,
        )
        in_mfm = []
        if do_overnight_mfm_out:
            in_mfm = _filter_and_trim(
                flight_adapter.search_oneway(macau_iata, origin, return_, pax, max_stops),
                earliest, latest, top_k_flight, max_dur,
            )

        if not out_hkg or (not in_hkg and not in_mfm):
            continue

        # 호텔
        hk_hotels = hotel_adapter.search("Hong Kong", arrival, return_, rooms, pax)
        hk_hotels = [h for h in hk_hotels if h.review_score >= hotel_cfg["min_review_score"]]
        hk_hotels = hk_hotels[:top_k_hotel]
        if not hk_hotels:
            continue

        macau_hotels: List[Hotel] = []
        if macau_overnight_used:
            # 마카오에서는 마지막 박만 (= 귀국 전날 체크인)
            mc_in = return_ - timedelta(days=1)
            mc_out = return_
            macau_hotels = hotel_adapter.search("Macau", mc_in, mc_out, rooms, pax)
            macau_hotels = [h for h in macau_hotels if h.review_score >= macau_min_score]
            macau_hotels = macau_hotels[:macau_top_k]

        # --- A: day_trip (HKG↔HKG, 4박 HK, 마카오 페리 왕복 당일치기) ---
        if do_day_trip and in_hkg:
            for itin in _pair(out_hkg, in_hkg, pax):
                for h in hk_hotels:
                    cand = TripCandidate(
                        arrival=arrival, return_date=return_,
                        nights=nights, pax=pax, rooms=rooms,
                        flight=itin,
                        hk_hotel=h, hk_nights=nights,
                        side_trip_krw_per_pax=ferry_rt,
                        fx_buffer_pct=fx_buffer,
                        scenario="day_trip",
                    )
                    if cand.cost_per_person_krw <= budget:
                        candidates.append(cand)

        # --- B: overnight_rt ((N-1)박 HK + 1박 Macau, HKG↔HKG, 페리 왕복) ---
        if do_overnight_rt and in_hkg and macau_hotels and nights >= 2:
            for itin in _pair(out_hkg, in_hkg, pax):
                for hk in hk_hotels:
                    for mc in macau_hotels:
                        cand = TripCandidate(
                            arrival=arrival, return_date=return_,
                            nights=nights, pax=pax, rooms=rooms,
                            flight=itin,
                            hk_hotel=hk, hk_nights=nights - 1,
                            macau_hotel=mc, macau_nights=1,
                            side_trip_krw_per_pax=ferry_rt,
                            fx_buffer_pct=fx_buffer,
                            scenario="overnight_rt",
                        )
                        if cand.cost_per_person_krw <= budget:
                            candidates.append(cand)

        # --- C: overnight_mfm_out ((N-1)박 HK + 1박 Macau, ICN→HKG / MFM→ICN, 페리 편도) ---
        if do_overnight_mfm_out and in_mfm and macau_hotels and nights >= 2:
            for itin in _pair(out_hkg, in_mfm, pax):
                for hk in hk_hotels:
                    for mc in macau_hotels:
                        cand = TripCandidate(
                            arrival=arrival, return_date=return_,
                            nights=nights, pax=pax, rooms=rooms,
                            flight=itin,
                            hk_hotel=hk, hk_nights=nights - 1,
                            macau_hotel=mc, macau_nights=1,
                            side_trip_krw_per_pax=ferry_ow,
                            fx_buffer_pct=fx_buffer,
                            scenario="overnight_mfm_out",
                        )
                        if cand.cost_per_person_krw <= budget:
                            candidates.append(cand)

    score_candidates(candidates, rank_cfg["weights"], hotel_cfg["preferred_districts"])
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[: rank_cfg.get("top_n", 10)]
