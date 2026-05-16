"""결과 출력: Markdown 리포트 + CSV."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from .models import TripCandidate


def _krw(n: int) -> str:
    return f"₩{n:,}"


def render_markdown(candidates: List[TripCandidate], budget_per_pax: int) -> str:
    lines: List[str] = []
    lines.append("# 홍콩 여행 최적화 결과\n")
    lines.append(f"- 예산: 1인 {_krw(budget_per_pax)}")
    lines.append(f"- 후보 수: {len(candidates)}건\n")

    if not candidates:
        lines.append("> 예산·제약을 만족하는 후보를 찾지 못했습니다. config.yaml의 budget 또는 제약을 조정해 보세요.\n")
        return "\n".join(lines)

    for i, c in enumerate(candidates, 1):
        f = c.flight
        h = c.hotel
        lines.append(f"## Rank {i}  ·  {_krw(c.cost_per_person_krw)}/인  ·  점수 {c.score:.3f}")
        lines.append("")
        lines.append(f"- 일정: **{c.arrival:%Y-%m-%d(%a)} 도착 → {c.return_date:%Y-%m-%d(%a)} 귀국** · {c.nights}박")
        lines.append("")
        lines.append("**✈ 항공편**")
        lines.append(
            f"- 출국 {f.outbound.carrier} {f.outbound.flight_no}  "
            f"{f.outbound.depart_airport} {f.outbound.depart_time:%m/%d %H:%M} → "
            f"{f.outbound.arrive_airport} {f.outbound.arrive_time:%H:%M}  "
            f"({'직항' if f.outbound.stops == 0 else f'{f.outbound.stops}경유'}, {f.outbound.duration_min}분)"
        )
        lines.append(
            f"- 귀국 {f.inbound.carrier} {f.inbound.flight_no}  "
            f"{f.inbound.depart_airport} {f.inbound.depart_time:%m/%d %H:%M} → "
            f"{f.inbound.arrive_airport} {f.inbound.arrive_time:%H:%M}  "
            f"({'직항' if f.inbound.stops == 0 else f'{f.inbound.stops}경유'}, {f.inbound.duration_min}분)"
        )
        lines.append(f"- 항공 합계: {_krw(f.total_price_krw)} (4인)")
        lines.append("")
        lines.append("**🏨 숙소**")
        lines.append(
            f"- {h.name}  {h.star:.1f}★  평점 {h.review_score:.1f}  "
            f"· {h.district} · MTR {h.distance_to_mtr_m}m"
        )
        lines.append(
            f"- {_krw(h.price_per_room_per_night_krw)}/박/객실 × {c.nights}박 × {c.rooms}객실 "
            f"= {_krw(c.hotel_cost_krw)}  ·  환불 {'가능' if h.refundable else '불가'}"
        )
        lines.append("")
        lines.append("**🚢 부가**")
        lines.append(f"- 마카오 페리·기타: {_krw(c.side_trip_cost_krw)}/인 × {c.pax} = {_krw(c.side_trip_cost_krw * c.pax)}")
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
            "rank", "score", "cost_per_pax_krw", "total_krw",
            "depart_date", "return_date",
            "out_carrier", "out_no", "out_depart", "out_arrive", "out_stops",
            "in_carrier", "in_no", "in_depart", "in_arrive", "in_stops",
            "flight_total_krw",
            "hotel_name", "hotel_star", "hotel_score", "district",
            "mtr_m", "hotel_total_krw", "refundable",
        ])
        for i, c in enumerate(candidates, 1):
            f, h = c.flight, c.hotel
            w.writerow([
                i, c.score, c.cost_per_person_krw, c.total_cost_krw,
                c.arrival.isoformat(), c.return_date.isoformat(),
                f.outbound.carrier, f.outbound.flight_no,
                f.outbound.depart_time.isoformat(), f.outbound.arrive_time.isoformat(),
                f.outbound.stops,
                f.inbound.carrier, f.inbound.flight_no,
                f.inbound.depart_time.isoformat(), f.inbound.arrive_time.isoformat(),
                f.inbound.stops,
                f.total_price_krw,
                h.name, h.star, h.review_score, h.district,
                h.distance_to_mtr_m, c.hotel_cost_krw, h.refundable,
            ])
