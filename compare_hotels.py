"""두 호텔 (빅토리아 호텔 vs 아이클럽 셩완 호텔) 비교.

- 캐시에서 두 호텔 메타데이터 추출 (이름·가격·평점·amenities·링크 등)
- 지도 2개 마커, 큰 정보 박스 (호텔명 + 가격 + 평점 + 지역 + 환불)
- 일정 후보 가격 비교 (빅토리아는 top 10에 있음, 아이클럽은 같은 날짜 가정해서 환산)
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from staticmap import CircleMarker, StaticMap

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
CACHE_DB = ROOT / "cache.db"

NEEDLES = {
    "빅토리아": ["빅토리아 호텔", "Victoria"],
    "아이클럽": ["아이클럽 셩완 호텔", "iclub Sheung Wan", "iClub Sheung Wan"],
}


def all_hotel_responses() -> List[Tuple[Dict, str, str]]:
    """캐시에서 모든 HK google_hotels 응답 → [(json, checkin, checkout), ...]"""
    con = sqlite3.connect(CACHE_DB)
    rows = con.execute("SELECT key, value FROM cache WHERE key LIKE 'serpapi:%'").fetchall()
    con.close()
    out = []
    for key, val in rows:
        if "engine=google_hotels" not in key:
            continue
        if "macau" in key.lower():
            continue
        params = dict(p.split("=", 1) for p in key.replace("serpapi:", "").split("&") if "=" in p)
        out.append((json.loads(val), params.get("check_in_date", ""), params.get("check_out_date", "")))
    return out


def find_hotel(label_key: str) -> Optional[Tuple[Dict, str, str]]:
    """label_key (빅토리아|아이클럽)에 매칭되는 호텔의 (meta, checkin, checkout)."""
    needles = NEEDLES[label_key]
    for resp, ci, co in all_hotel_responses():
        for p in resp.get("properties") or []:
            name = p.get("name", "")
            if any(n in name for n in needles):
                return p, ci, co
    return None


def krw(n: int) -> str:
    return f"₩{n:,}"


# ---- 지도 렌더 -------------------------------------------------------------

def _font(size: int) -> ImageFont.ImageFont:
    for path in ["C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/Arial.ttf"]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


_COLORS = ["#E74C3C", "#3498DB"]


def render_compare_map(hotels: List[Dict], out_png: Path,
                       width: int = 2000, height: int = 1400) -> None:
    """2개 마커 + 큰 정보 박스 (호텔명·가격·평점·지역·환불·체크인)."""
    coords = [(h["meta"]["gps_coordinates"]["longitude"],
               h["meta"]["gps_coordinates"]["latitude"]) for h in hotels]
    lats = [c[1] for c in coords]
    lons = [c[0] for c in coords]
    # 두 점만 — 적절한 패딩
    lat_pad = max(0.008, (max(lats) - min(lats)) * 0.6)
    lon_pad = max(0.012, (max(lons) - min(lons)) * 0.6)
    bbox = (min(lons) - lon_pad, min(lats) - lat_pad,
            max(lons) + lon_pad, max(lats) + lat_pad)

    m = StaticMap(width, height, padding_x=80, padding_y=80,
                  url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
                  headers={"User-Agent": "trip-optimizer/0.3"})
    m.add_marker(CircleMarker((bbox[0], bbox[1]), "#00000000", 1))
    m.add_marker(CircleMarker((bbox[2], bbox[3]), "#00000000", 1))
    for i, (lon, lat) in enumerate(coords):
        m.add_marker(CircleMarker((lon, lat), "white", 70))
        m.add_marker(CircleMarker((lon, lat), _COLORS[i], 58))
    img = m.render()

    draw = ImageDraw.Draw(img)
    font_num = _font(54)
    font_title = _font(28)
    font_body = _font(22)
    font_small = _font(18)

    import math
    def lonlat_to_xy(lon, lat):
        zoom = m.zoom
        x = (lon + 180.0) / 360.0 * (2 ** zoom) * 256
        sin_lat = math.sin(math.radians(lat))
        y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * (2 ** zoom) * 256
        px = x - (m.x_center * 256 - width / 2)
        py = y - (m.y_center * 256 - height / 2)
        return int(px), int(py)

    # 마커 번호
    for i, (lon, lat) in enumerate(coords):
        px, py = lonlat_to_xy(lon, lat)
        label = str(i + 1)
        bbox_t = draw.textbbox((0, 0), label, font=font_num)
        tw, th = bbox_t[2] - bbox_t[0], bbox_t[3] - bbox_t[1]
        draw.text((px - tw // 2, py - th // 2 - 6), label, fill="white", font=font_num)

    # 정보 박스 (좌/우 배치)
    for i, h in enumerate(hotels):
        meta = h["meta"]
        lon, lat = coords[i]
        px, py = lonlat_to_xy(lon, lat)
        color = _COLORS[i]
        rr, gg, bb = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)

        # 박스 내용
        lines = [
            (h["display_name"], font_title, True),
            (f"{krw(h['price_per_night'])} / 박 (1객실)", font_body, True),
            (f"평점 {h['score10']:.1f}/10  ·  {h['district']}", font_body, False),
            (f"종류: {meta.get('type', '-')}  ·  별 {h['star']:.1f}★", font_small, False),
            (f"체크인 {meta.get('check_in_time', '-')}  /  체크아웃 {meta.get('check_out_time', '-')}", font_small, False),
            (f"환불: {'가능' if h['refundable'] else '불가'}", font_small, False),
        ]
        # amenities 일부 (최대 4개)
        amenities = meta.get("amenities") or []
        if amenities:
            am_short = " · ".join(amenities[:4])
            lines.append((f"편의: {am_short[:50]}", font_small, False))

        # 박스 크기 계산
        widths = []
        heights = []
        for text, font, _bold in lines:
            tb = draw.textbbox((0, 0), text, font=font)
            widths.append(tb[2] - tb[0])
            heights.append(tb[3] - tb[1])
        box_w = max(widths) + 36
        box_h = sum(heights) + 22 + 14 * (len(lines) - 1)
        # 위치: 마커 기준 우측 또는 좌측
        gap = 50
        if i == 0:
            # 왼쪽 호텔: 박스를 마커 좌측에
            x0 = max(20, px - gap - box_w)
            y0 = max(20, py - box_h // 2)
        else:
            # 오른쪽 호텔: 박스를 마커 우측에
            x0 = min(width - box_w - 20, px + gap)
            y0 = max(20, py - box_h // 2)

        # 흰 배경 + 검은 외곽
        draw.rectangle((x0, y0, x0 + box_w, y0 + box_h),
                       fill="white", outline="black", width=4)
        # 좌측 색상 바
        draw.rectangle((x0, y0, x0 + 14, y0 + box_h), fill=(rr, gg, bb))

        # 텍스트 그리기
        ty = y0 + 14
        for (text, font, bold), th in zip(lines, heights):
            fill = (rr, gg, bb) if bold and (text == h["display_name"]) else "black"
            draw.text((x0 + 24, ty), text, fill=fill, font=font)
            ty += th + 14

        # 연결선 (마커 → 박스 모서리)
        if i == 0:
            box_anchor = (x0 + box_w, y0 + box_h // 2)
        else:
            box_anchor = (x0, y0 + box_h // 2)
        draw.line([(px, py), box_anchor], fill=(rr, gg, bb), width=4)

    img.save(out_png)


# ---- main ------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    h1 = find_hotel("빅토리아")
    h2 = find_hotel("아이클럽")
    if not h1 or not h2:
        missing = []
        if not h1: missing.append("빅토리아")
        if not h2: missing.append("아이클럽")
        print(f"[ERROR] 캐시에서 못 찾음: {missing}")
        sys.exit(1)

    rooms = 2

    def normalize(label, found) -> Dict:
        meta, ci, co = found
        # 가격
        rpn = meta.get("rate_per_night") or {}
        per_night = int(rpn.get("extracted_lowest") or 0)
        # 평점
        score = float(meta.get("overall_rating") or 0)
        score10 = score * 2 if 0 < score <= 5 else score
        # 별
        hc = meta.get("extracted_hotel_class") or meta.get("hotel_class") or 0
        try:
            star = float(hc) if hc else 3.0
        except (TypeError, ValueError):
            star = 3.0
        # 위치
        gps = meta.get("gps_coordinates") or {}
        lat, lon = gps.get("latitude"), gps.get("longitude")
        # 지역
        import math
        HK_CENTERS = {
            "Tsim Sha Tsui": (22.2987, 114.1722),
            "Causeway Bay":  (22.2803, 114.1830),
            "Central":       (22.2820, 114.1582),
            "Mongkok":       (22.3193, 114.1694),
            "Jordan":        (22.3049, 114.1722),
            "Wan Chai":      (22.2774, 114.1716),
            "Sheung Wan":    (22.2873, 114.1502),
        }
        best, bd = "Other", float("inf")
        if lat and lon:
            cos_lat = math.cos(math.radians(lat))
            for name, (cl, clo) in HK_CENTERS.items():
                d = math.hypot((lon - clo) * cos_lat * 111, (lat - cl) * 111)
                if d < bd:
                    best, bd = name, d
            if bd > 2.0:
                best = "Other"
        return {
            "display_name": meta.get("name", label),
            "meta": meta,
            "checkin": ci, "checkout": co,
            "price_per_night": per_night,
            "score10": round(score10, 1),
            "star": star,
            "district": best,
            "refundable": bool(meta.get("free_cancellation")),
            "label_key": label,
        }

    h1n = normalize("빅토리아", h1)
    h2n = normalize("아이클럽", h2)

    print(f"호텔 1: {h1n['display_name']}  ₩{h1n['price_per_night']:,}/박  평점 {h1n['score10']}  {h1n['district']}")
    print(f"호텔 2: {h2n['display_name']}  ₩{h2n['price_per_night']:,}/박  평점 {h2n['score10']}  {h2n['district']}")
    print(f"\n캐시 일자: 호텔1 {h1n['checkin']}~{h1n['checkout']}  /  호텔2 {h2n['checkin']}~{h2n['checkout']}")

    # 지도
    map_png = RESULTS_DIR / "compare_map.png"
    render_compare_map([h1n, h2n], map_png)
    print(f"\n지도 → {map_png}")

    # 비교용 trip cost 추정
    # 빅토리아: results.csv에서 가장 싼 후보
    # 아이클럽: 같은 날짜의 best flight + 자기 호텔비 + 페리 (당일치기 기준)
    with open(RESULTS_DIR / "results.csv", encoding="utf-8") as f:
        trips = list(csv.DictReader(f))

    # 빅토리아가 들어간 가장 싼 후보 (= 빅토리아 day_trip 기준)
    vic_day = min((t for t in trips if "빅토리아" in t["hk_hotel"] and t["scenario"] == "day_trip"),
                  key=lambda t: int(t["cost_per_pax_krw"]), default=None)
    if vic_day:
        # 같은 항공 조합 + 빅토리아 → 아이클럽으로 호텔만 바꿔 추정
        flight_per_pax = int(vic_day["flight_total_krw"]) // 4
        nights = int(vic_day["hk_nights"])
        ferry_per_pax = int(vic_day["side_trip_per_pax_krw"])
        # 아이클럽 가격 — 같은 날짜의 다른 캐시 응답일 수도 있지만 단가는 비슷하다고 가정
        vic_total = flight_per_pax + (h1n["price_per_night"] * nights * rooms) // 4 + ferry_per_pax
        iclub_total = flight_per_pax + (h2n["price_per_night"] * nights * rooms) // 4 + ferry_per_pax
        # 환율 버퍼 3%
        vic_total = int(vic_total * 1.03)
        iclub_total = int(iclub_total * 1.03)
        print(f"\n같은 항공편 ({vic_day['out_carrier']} {vic_day['out_no']} / {vic_day['in_carrier']} {vic_day['in_no']}) + 4박 + day_trip 가정 시:")
        print(f"  빅토리아 1인 총비용: ₩{vic_total:,}")
        print(f"  아이클럽 1인 총비용: ₩{iclub_total:,}")
        print(f"  차이: ₩{iclub_total - vic_total:,}/인 × 4 = ₩{(iclub_total - vic_total) * 4:,} (4인 총합)")


if __name__ == "__main__":
    main()
