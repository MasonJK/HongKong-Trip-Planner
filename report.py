"""결과 출력: Markdown 리포트 + CSV."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from .models import TripCandidate


_SCENARIO_LABEL = {
    "day_trip":          "마카오 당일치기 (HKG ↔ HKG)",
    "overnight_rt":      "마카오 1박 + HK · 페리 왕복 (HKG ↔ HKG)",
    "overnight_mfm_out": "마카오 1박 + HK · 페리 편도 (HKG 입국 / MFM 귀국)",
}


def _krw(n: int) -> str:
    return f"₩{n:,}"


def _fmt_flight(f, role: str) -> str:
    return (
        f"- {role} {f.carrier} {f.flight_no}  "
        f"{f.depart_airport} {f.depart_time:%m/%d %H:%M} → "
        f"{f.arrive_airport} {f.arrive_time:%H:%M}  "
        f"({'직항' if f.stops == 0 else f'{f.stops}경유'}, {f.duration_min}분)"
    )


def render_markdown(candidates: List[TripCandidate], budget_per_pax: int) -> str:
    lines: List[str] = []
    lines.append("# 홍콩 여행 최적화 결과\n")
    lines.append(f"- 예산: 1인 {_krw(budget_per_pax)}")
    lines.append(f"- 후보 수: {len(candidates)}건\n")

    if not candidates:
        lines.append("> 예산·제약을 만족하는 후보를 찾지 못했습니다. config.yaml의 budget 또는 제약을 조정해 보세요.\n")
        return "\n".join(lines)

    for i, c in enumerate(candidates, 1):
        f, hk = c.flight, c.hk_hotel
        lines.append(f"## Rank {i}  ·  {_krw(c.cost_per_person_krw)}/인  ·  점수 {c.score:.3f}")
        lines.append("")
        lines.append(f"- **시나리오**: {_SCENARIO_LABEL.get(c.scenario, c.scenario)}")
        lines.append(f"- 일정: **{c.arrival:%Y-%m-%d(%a)} 도착 → {c.return_date:%Y-%m-%d(%a)} 귀국** · {c.nights}박")
        lines.append("")
        lines.append("**✈ 항공편**")
        lines.append(_fmt_flight(f.outbound, "출국"))
        lines.append(_fmt_flight(f.inbound, "귀국"))
        lines.append(f"- 항공 합계: {_krw(f.total_price_krw)} ({c.pax}인)")
        lines.append("")
        lines.append("**🏨 숙소 (HK)**")
        lines.append(
            f"- {hk.name}  {hk.star:.1f}★  평점 {hk.review_score:.1f}  "
            f"· {hk.district} · MTR {hk.distance_to_mtr_m}m"
        )
        lines.append(
            f"- {_krw(hk.price_per_room_per_night_krw)}/박/객실 × {c.hk_nights}박 × {c.rooms}객실 "
            f"= {_krw(c.hk_hotel_cost_krw)}  ·  환불 {'가능' if hk.refundable else '불가'}"
        )
        if c.macau_hotel:
            mc = c.macau_hotel
            lines.append("")
            lines.append("**🏨 숙소 (마카오)**")
            lines.append(
                f"- {mc.name}  {mc.star:.1f}★  평점 {mc.review_score:.1f}  · {mc.district}"
            )
            lines.append(
                f"- {_krw(mc.price_per_room_per_night_krw)}/박/객실 × {c.macau_nights}박 × {c.rooms}객실 "
                f"= {_krw(c.macau_hotel_cost_krw)}  ·  환불 {'가능' if mc.refundable else '불가'}"
            )
        lines.append("")
        lines.append("**🚢 부가**")
        lines.append(f"- 페리·세금 등: {_krw(c.side_trip_krw_per_pax)}/인 × {c.pax} = {_krw(c.side_trip_krw_per_pax * c.pax)}")
        lines.append("")
        lines.append(
            f"**합계**: {_krw(c.subtotal_krw)} + 환율버퍼 {c.fx_buffer_pct}% "
            f"= **{_krw(c.total_cost_krw)}**  →  1인 {_krw(c.cost_per_person_krw)}"
        )
        sb = c.score_breakdown
        lines.append(
            f"<details><summary>점수 세부</summary>\n\n"
            f"- 비용 {sb['cost']} · 비행시간 {sb['flight_time']} · 호텔 {sb['hotel_score']} · "
            f"위치 {sb['location']} · 취소유연성 {sb['cancel_flex']}\n\n</details>"
        )
        lines.append("\n---\n")

    return "\n".join(lines)


def write_csv(candidates: List[TripCandidate], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "rank", "scenario", "score", "cost_per_pax_krw", "total_krw",
            "arrival", "return",
            "out_carrier", "out_no", "out_from", "out_to", "out_depart", "out_arrive", "out_stops",
            "in_carrier", "in_no", "in_from", "in_to", "in_depart", "in_arrive", "in_stops",
            "flight_total_krw",
            "hk_hotel", "hk_district", "hk_nights", "hk_total_krw",
            "macau_hotel", "macau_nights", "macau_total_krw",
            "side_trip_per_pax_krw",
        ])
        for i, c in enumerate(candidates, 1):
            f = c.flight
            hk = c.hk_hotel
            mc = c.macau_hotel
            w.writerow([
                i, c.scenario, c.score, c.cost_per_person_krw, c.total_cost_krw,
                c.arrival.isoformat(), c.return_date.isoformat(),
                f.outbound.carrier, f.outbound.flight_no,
                f.outbound.depart_airport, f.outbound.arrive_airport,
                f.outbound.depart_time.isoformat(), f.outbound.arrive_time.isoformat(),
                f.outbound.stops,
                f.inbound.carrier, f.inbound.flight_no,
                f.inbound.depart_airport, f.inbound.arrive_airport,
                f.inbound.depart_time.isoformat(), f.inbound.arrive_time.isoformat(),
                f.inbound.stops,
                f.total_price_krw,
                hk.name, hk.district, c.hk_nights, c.hk_hotel_cost_krw,
                (mc.name if mc else ""), c.macau_nights, c.macau_hotel_cost_krw,
                c.side_trip_krw_per_pax,
            ])
