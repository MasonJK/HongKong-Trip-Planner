"""Top 7 호텔을 지도에 표시한 PNG + PPT 보고서 생성.

results.csv (옵티마이저 출력) + cache.db (SerpAPI 캐시) 를 입력으로 받아:
  1. top 7 HK 호텔 선정 (평점·가격·위치 가중)
  2. staticmap + OSM 타일로 지도 PNG
  3. python-pptx로 보고서 PPT

후속 실행: 같은 일자 검색이 캐시에 있으면 API 호출 0건.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from staticmap import CircleMarker, IconMarker, StaticMap

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
CACHE_DB = ROOT / "cache.db"

# 선호 지역 가중치
PREFERRED_DISTRICTS = {"Tsim Sha Tsui", "Central", "Causeway Bay", "Jordan"}

# HK 지역 중심 (district 추론용 — serpapi.py와 동일)
HK_DISTRICT_CENTERS = {
    "Tsim Sha Tsui": (22.2987, 114.1722),
    "Causeway Bay":  (22.2803, 114.1830),
    "Central":       (22.2820, 114.1582),
    "Mongkok":       (22.3193, 114.1694),
    "Jordan":        (22.3049, 114.1722),
    "Wan Chai":      (22.2774, 114.1716),
}


@dataclass
class HotelRec:
    name: str
    star: float
    review_score: float
    district: str
    price_per_night: int
    lat: float
    lon: float
    refundable: bool


# ---- 캐시 조회 -----------------------------------------------------------

def load_hotel_response(checkin: str, checkout: str) -> Optional[Dict]:
    """cache.db에서 (checkin, checkout)에 해당하는 호텔 검색 응답을 찾아 JSON 반환."""
    con = sqlite3.connect(CACHE_DB)
    rows = con.execute("SELECT key, value FROM cache WHERE key LIKE 'serpapi:%'").fetchall()
    con.close()
    needle = f"check_in_date={checkin}&check_out_date={checkout}"
    # Macau가 아닌 HK 검색만 — q=Hong Kong hotels가 정답
    for key, val in rows:
        if needle in key and ("q=Hong+Kong" in key or "q=Hong%20Kong" in key):
            return json.loads(val)
    return None


def district_from_gps(lat: float, lon: float, max_km: float = 2.0) -> str:
    cos_lat = math.cos(math.radians(lat))
    best_name, best_d = "Other", float("inf")
    for name, (clat, clon) in HK_DISTRICT_CENTERS.items():
        dx = (lon - clon) * cos_lat * 111
        dy = (lat - clat) * 111
        d = math.hypot(dx, dy)
        if d < best_d:
            best_name, best_d = name, d
    return best_name if best_d <= max_km else "Other"


def parse_hotels(resp: Dict) -> List[HotelRec]:
    out: List[HotelRec] = []
    for p in resp.get("properties") or []:
        gps = p.get("gps_coordinates") or {}
        lat = gps.get("latitude")
        lon = gps.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue

        # 가격
        per_night = 0
        rpn = p.get("rate_per_night") or {}
        tot = p.get("total_rate") or {}
        if rpn.get("extracted_lowest"):
            per_night = int(rpn["extracted_lowest"])
        elif tot.get("extracted_lowest"):
            # total/(rooms*nights) — 캐시에서 nights/rooms 모르므로 보수적으로 통째
            per_night = int(tot["extracted_lowest"])
        if per_night == 0:
            continue

        # 평점 0~5 → 0~10
        score = float(p.get("overall_rating") or 0)
        if 0 < score <= 5:
            score *= 2

        # 별
        hc = p.get("extracted_hotel_class") or p.get("hotel_class")
        star = 3.0
        if isinstance(hc, (int, float)):
            star = float(hc)
        elif isinstance(hc, str):
            m = re.search(r"(\d(?:\.\d)?)", hc)
            if m:
                star = float(m.group(1))

        out.append(HotelRec(
            name=p.get("name") or "Unknown",
            star=star,
            review_score=round(score, 1),
            district=district_from_gps(lat, lon),
            price_per_night=per_night,
            lat=float(lat),
            lon=float(lon),
            refundable=bool(p.get("free_cancellation")),
        ))
    return out


def score_hotel(h: HotelRec, price_lo: int, price_hi: int) -> float:
    """가성비 위주 종합 점수.
       - cost: 50% (싸면 좋음)
       - review: 35% (높으면 좋음, 8.0 미만은 패널티)
       - location: 15% (선호 지역이면 만점)
    """
    if price_hi <= price_lo:
        s_cost = 0.5
    else:
        s_cost = 1.0 - (h.price_per_night - price_lo) / (price_hi - price_lo)
    s_cost = max(0.0, min(1.0, s_cost))

    s_rev = max(0.0, min(1.0, (h.review_score - 6.0) / 4.0))  # 6.0=0, 10.0=1
    s_loc = 1.0 if h.district in PREFERRED_DISTRICTS else 0.4

    return round(0.50 * s_cost + 0.35 * s_rev + 0.15 * s_loc, 3)


# ---- 지도 렌더 ------------------------------------------------------------

def _try_font(size: int) -> ImageFont.ImageFont:
    """Windows에 흔한 한글 가능 폰트 시도."""
    candidates = [
        "C:/Windows/Fonts/malgun.ttf",     # 맑은고딕
        "C:/Windows/Fonts/MalgunGothicBold.ttf",
        "C:/Windows/Fonts/gulim.ttc",
        "C:/Windows/Fonts/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


# 7개 마커용 색상 팔레트 (HSV 둥글게)
_PALETTE = [
    "#E74C3C", "#3498DB", "#2ECC71", "#F39C12",
    "#9B59B6", "#1ABC9C", "#34495E",
]


def render_map(hotels: List[HotelRec], out_png: Path,
               width: int = 2000, height: int = 1400) -> None:
    # 호텔 분포에 약간의 패딩만 줘서 타이트하게
    lats = [h.lat for h in hotels]
    lons = [h.lon for h in hotels]
    lat_pad = max(0.005, (max(lats) - min(lats)) * 0.25)
    lon_pad = max(0.008, (max(lons) - min(lons)) * 0.25)
    bbox = (min(lons) - lon_pad, min(lats) - lat_pad,
            max(lons) + lon_pad, max(lats) + lat_pad)

    m = StaticMap(width, height, padding_x=60, padding_y=60,
                  url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
                  headers={"User-Agent": "trip-optimizer/0.3"})
    # bbox 강제용 corner marker (투명)
    m.add_marker(CircleMarker((bbox[0], bbox[1]), "#00000000", 1))
    m.add_marker(CircleMarker((bbox[2], bbox[3]), "#00000000", 1))
    for i, h in enumerate(hotels):
        color = _PALETTE[i % len(_PALETTE)]
        m.add_marker(CircleMarker((h.lon, h.lat), "white", 56))
        m.add_marker(CircleMarker((h.lon, h.lat), color, 46))
    img = m.render()

    # 마커에 번호 표시
    draw = ImageDraw.Draw(img)
    font_num = _try_font(40)
    font_lbl = _try_font(32)

    # staticmap 내부 좌표 변환 함수를 직접 못 써서, 픽셀 계산을 다시 함
    def lonlat_to_xy(lon, lat):
        zoom = m.zoom
        # Web Mercator
        x = (lon + 180.0) / 360.0 * (2 ** zoom) * 256
        sin_lat = math.sin(math.radians(lat))
        y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * (2 ** zoom) * 256
        # 지도 좌상단(픽셀)
        px = x - (m.x_center * 256 - width / 2)
        py = y - (m.y_center * 256 - height / 2)
        return int(px), int(py)

    # 마커 위치 미리 계산 + 겹침 해소를 위해 가까운 마커끼리는 라벨 방향 분산
    positions = [lonlat_to_xy(h.lon, h.lat) for h in hotels]

    def label_offset(i: int) -> tuple:
        """근처에 다른 마커가 있으면 라벨을 위/아래/좌/우로 분산."""
        px, py = positions[i]
        # 동/서/북/남/북동/북서 6방향
        dirs = [
            (66, -22),    # 동
            (-66, -22),   # 서 (라벨 폭만큼 더 왼쪽으로 빼야 함 → 텍스트 그릴 때 처리)
            (0, -82),     # 북
            (0, 60),      # 남
            (66, -82),    # 북동
            (-66, 60),    # 남서
        ]
        # 가장 가까운 다른 마커와의 거리로 방향 선택 (단순 라운드로빈)
        return dirs[i % len(dirs)]

    for i, h in enumerate(hotels):
        px, py = positions[i]
        label = str(i + 1)
        bbox = draw.textbbox((0, 0), label, font=font_num)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((px - tw // 2, py - th // 2 - 4), label, fill="white", font=font_num)

        # 가격 박스 (마커 주변 여러 방향으로 분산)
        price_text = f"KRW {h.price_per_night // 1000}k"
        bbox = draw.textbbox((0, 0), price_text, font=font_lbl)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        ox, oy = label_offset(i)
        # 서/남서 방향은 라벨 너비만큼 왼쪽 보정
        if ox < 0:
            x0 = px + ox - tw
        else:
            x0 = px + ox
        y0 = py + oy
        pad_x, pad_y = 14, 10
        # 흰 박스 + 굵은 검은 외곽 + 색상 매칭 좌측 바
        color = _PALETTE[i % len(_PALETTE)]
        rr, gg, bb = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        box_rect = (x0 - pad_x, y0 - pad_y, x0 + tw + pad_x, y0 + th + pad_y)
        draw.rectangle(box_rect, fill="white", outline="black", width=3)
        # 좌측 색상 바 (마커 색과 매칭)
        draw.rectangle((box_rect[0], box_rect[1], box_rect[0] + 8, box_rect[3]),
                       fill=(rr, gg, bb))
        draw.text((x0, y0), price_text, fill="black", font=font_lbl)

    img.save(out_png)


# ---- PPT 빌더 -------------------------------------------------------------

def build_pptx(top_trips: List[Dict], hotels: List[HotelRec], map_png: Path,
               budget_per_pax: int, out_pptx: Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.enum.text import PP_ALIGN
    from pptx.dml.color import RGBColor

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    blank_layout = prs.slide_layouts[6]
    bg_dark = RGBColor(0x1A, 0x1F, 0x2E)
    bg_light = RGBColor(0xF5, 0xF7, 0xFA)
    txt_dark = RGBColor(0x1A, 0x1F, 0x2E)
    txt_muted = RGBColor(0x55, 0x60, 0x6E)
    accent = RGBColor(0xE7, 0x4C, 0x3C)

    def add_textbox(slide, x, y, w, h, text, size=18, bold=False, color=txt_dark, align=PP_ALIGN.LEFT):
        tb = slide.shapes.add_textbox(x, y, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = Emu(0); tf.margin_right = Emu(0)
        tf.margin_top = Emu(0); tf.margin_bottom = Emu(0)
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        run.font.name = "Malgun Gothic"
        return tb

    def add_bg(slide, color):
        from pptx.oxml.ns import qn
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = color

    # ----- Slide 1: Title -----
    s = prs.slides.add_slide(blank_layout)
    add_bg(s, bg_dark)
    add_textbox(s, Inches(0.7), Inches(2.5), Inches(12), Inches(1.2),
                "홍콩 여행 최적화 보고서", size=48, bold=True,
                color=RGBColor(0xFF, 0xFF, 0xFF))
    add_textbox(s, Inches(0.7), Inches(3.6), Inches(12), Inches(0.6),
                "4인 · 4박 5일 · 2026년 10~11월", size=22,
                color=RGBColor(0xC8, 0xCD, 0xD7))
    add_textbox(s, Inches(0.7), Inches(4.4), Inches(12), Inches(0.5),
                f"예산 1인 ₩{budget_per_pax:,}  ·  실시간 SerpAPI 데이터 기반",
                size=14, color=RGBColor(0x9A, 0xA0, 0xAA))
    add_textbox(s, Inches(0.7), Inches(6.6), Inches(12), Inches(0.4),
                f"생성: {datetime.now():%Y-%m-%d %H:%M}", size=10,
                color=RGBColor(0x70, 0x76, 0x82))

    # ----- Slide 2: Top 3 trip candidates summary -----
    s = prs.slides.add_slide(blank_layout)
    add_bg(s, bg_light)
    add_textbox(s, Inches(0.5), Inches(0.3), Inches(12), Inches(0.6),
                "전체 일정 후보 Top 3", size=28, bold=True)
    add_textbox(s, Inches(0.5), Inches(0.95), Inches(12), Inches(0.35),
                "예산·항공시간·호텔 평점·위치·환불 유연성을 가중 평균한 종합 점수 기준",
                size=12, color=txt_muted)

    SCEN_LBL = {
        "day_trip": "마카오 당일치기",
        "overnight_rt": "마카오 1박 (페리 왕복)",
        "overnight_mfm_out": "마카오 1박 (MFM 귀국)",
    }
    card_y = Inches(1.5)
    card_h = Inches(1.85)
    for i, t in enumerate(top_trips[:3]):
        y = card_y + i * (card_h + Inches(0.18))
        rect = s.shapes.add_shape(1, Inches(0.5), y, Inches(12.3), card_h)
        rect.fill.solid()
        rect.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        rect.line.color.rgb = RGBColor(0xE0, 0xE4, 0xEA)
        rect.shadow.inherit = False

        # 순위
        add_textbox(s, Inches(0.7), y + Inches(0.15), Inches(0.8), Inches(0.7),
                    f"#{i+1}", size=32, bold=True, color=accent)
        # 시나리오 + 일정
        add_textbox(s, Inches(1.65), y + Inches(0.1), Inches(6.5), Inches(0.4),
                    SCEN_LBL.get(t['scenario'], t['scenario']),
                    size=12, bold=True, color=txt_muted)
        # 날짜
        arrival_dt = datetime.fromisoformat(t['arrival'])
        return_dt = datetime.fromisoformat(t['return'])
        wd_map = {0:'월',1:'화',2:'수',3:'목',4:'금',5:'토',6:'일'}
        dates_text = f"{arrival_dt:%Y-%m-%d}({wd_map[arrival_dt.weekday()]}) → {return_dt:%Y-%m-%d}({wd_map[return_dt.weekday()]})"
        add_textbox(s, Inches(1.65), y + Inches(0.5), Inches(6.5), Inches(0.5),
                    dates_text, size=18, bold=True)
        # 항공
        flight_text = f"✈ {t['out_carrier']} {t['out_no']} {t['out_from']}→{t['out_to']}  /  {t['in_carrier']} {t['in_no']} {t['in_from']}→{t['in_to']}"
        add_textbox(s, Inches(1.65), y + Inches(1.0), Inches(7), Inches(0.4),
                    flight_text, size=11, color=txt_muted)
        # 호텔
        hk = t.get('hk_hotel') or ''
        if t.get('macau_hotel'):
            hotel_text = f"🏨 HK: {hk} ({t['hk_nights']}박)  ·  마카오: {t['macau_hotel']} ({t['macau_nights']}박)"
        else:
            hotel_text = f"🏨 HK: {hk} ({t['hk_nights']}박)"
        add_textbox(s, Inches(1.65), y + Inches(1.35), Inches(8), Inches(0.4),
                    hotel_text, size=11, color=txt_muted)

        # 가격 (오른쪽)
        add_textbox(s, Inches(9.5), y + Inches(0.3), Inches(3.2), Inches(0.55),
                    f"₩{int(t['cost_per_pax_krw']):,}", size=28, bold=True,
                    color=txt_dark, align=PP_ALIGN.RIGHT)
        add_textbox(s, Inches(9.5), y + Inches(0.95), Inches(3.2), Inches(0.4),
                    "1인 총비용", size=11, color=txt_muted, align=PP_ALIGN.RIGHT)
        add_textbox(s, Inches(9.5), y + Inches(1.3), Inches(3.2), Inches(0.4),
                    f"종합 점수 {float(t['score']):.3f}", size=11, color=txt_muted, align=PP_ALIGN.RIGHT)

    # ----- Slide 3: Hotel map + table -----
    s = prs.slides.add_slide(blank_layout)
    add_bg(s, bg_light)
    add_textbox(s, Inches(0.5), Inches(0.3), Inches(12), Inches(0.6),
                "추천 호텔 Top 7", size=28, bold=True)
    add_textbox(s, Inches(0.5), Inches(0.95), Inches(12), Inches(0.35),
                "선호 지역 + 가성비 + 평점을 종합한 7곳. 지도 마커 번호와 표가 매칭.",
                size=12, color=txt_muted)

    # 지도
    s.shapes.add_picture(str(map_png), Inches(0.5), Inches(1.5), width=Inches(7.5))

    # 표 (오른쪽)
    table_left = Inches(8.2)
    table_top = Inches(1.5)
    table_w = Inches(4.6)
    rows = len(hotels) + 1
    table_h = Inches(0.4) + Inches(0.46) * len(hotels)
    tbl_shape = s.shapes.add_table(rows, 4, table_left, table_top, table_w, table_h)
    tbl = tbl_shape.table
    tbl.columns[0].width = Inches(0.4)
    tbl.columns[1].width = Inches(2.2)
    tbl.columns[2].width = Inches(0.9)
    tbl.columns[3].width = Inches(1.1)
    headers = ["#", "호텔", "1박", "평점/지역"]
    for j, h in enumerate(headers):
        cell = tbl.cell(0, j)
        cell.text = h
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor(0x1A, 0x1F, 0x2E)
        for p in cell.text_frame.paragraphs:
            for r in p.runs:
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                r.font.bold = True
                r.font.size = Pt(11)
                r.font.name = "Malgun Gothic"

    for i, h in enumerate(hotels):
        # # (색)
        c = tbl.cell(i + 1, 0)
        c.text = str(i + 1)
        c.fill.solid()
        rr, gg, bb = int(_PALETTE[i][1:3], 16), int(_PALETTE[i][3:5], 16), int(_PALETTE[i][5:7], 16)
        c.fill.fore_color.rgb = RGBColor(rr, gg, bb)
        for p in c.text_frame.paragraphs:
            for r in p.runs:
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                r.font.bold = True
                r.font.size = Pt(11)
                r.font.name = "Malgun Gothic"
                p.alignment = PP_ALIGN.CENTER

        # 호텔명
        c = tbl.cell(i + 1, 1)
        c.text = h.name[:22]
        for p in c.text_frame.paragraphs:
            for r in p.runs:
                r.font.size = Pt(10)
                r.font.name = "Malgun Gothic"
                r.font.color.rgb = txt_dark

        # 1박
        c = tbl.cell(i + 1, 2)
        c.text = f"₩{h.price_per_night:,}"
        for p in c.text_frame.paragraphs:
            for r in p.runs:
                r.font.size = Pt(10)
                r.font.name = "Malgun Gothic"
                r.font.color.rgb = txt_dark
                r.font.bold = True

        # 평점/지역
        c = tbl.cell(i + 1, 3)
        c.text = f"{h.review_score:.1f}  ·  {h.district}"
        for p in c.text_frame.paragraphs:
            for r in p.runs:
                r.font.size = Pt(9)
                r.font.name = "Malgun Gothic"
                r.font.color.rgb = txt_muted

    # ----- Slide 4: Decision factors -----
    s = prs.slides.add_slide(blank_layout)
    add_bg(s, bg_light)
    add_textbox(s, Inches(0.5), Inches(0.3), Inches(12), Inches(0.6),
                "결정 시 참고사항", size=28, bold=True)

    bullets = [
        "• 동일 날짜·항공편 조합 안에서 시나리오(당일치기/1박)만 다른 후보가 다수. 마카오 1박을 강하게 원하지 않는다면 day_trip이 비용·환불 유연성 모두 유리.",
        "• 평점 9점대 부티크(Hop Inn Nathan Rd 등)는 환불 불가 조건일 가능성 — 표의 '환불' 열을 보고 일정 확정 후 예약 권장.",
        "• 호텔 평점은 Google 0~5를 10점 환산. Booking.com 8.0+와 직접 비교는 1:1이 아님.",
        "• 항공편 max_duration_min=900분 필터 적용. 24h 1-stop 옵션은 자동 제외됨.",
        "• 좌표·가격은 SerpAPI 12시간 캐시. 실예약 시점에 가격 재확인 필요.",
    ]
    y = Inches(1.3)
    for b in bullets:
        tb = add_textbox(s, Inches(0.7), y, Inches(12), Inches(0.7), b, size=14)
        y += Inches(0.85)

    prs.save(out_pptx)


# ---- main ----------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    csv_path = RESULTS_DIR / "results.csv"
    if not csv_path.exists():
        print(f"[ERROR] {csv_path} 없음. 먼저 옵티마이저 실행하세요.")
        sys.exit(1)

    with open(csv_path, encoding="utf-8") as f:
        trips = list(csv.DictReader(f))
    if not trips:
        print("[ERROR] 후보 없음.")
        sys.exit(1)

    top = trips[0]
    checkin, checkout = top["arrival"], top["return"]
    print(f"top trip: {checkin} → {checkout} · scenario={top['scenario']} · 1인 ₩{int(top['cost_per_pax_krw']):,}")

    resp = load_hotel_response(checkin, checkout)
    if resp is None:
        print(f"[ERROR] cache.db에 {checkin}~{checkout} HK 호텔 응답 없음.")
        sys.exit(1)

    all_hotels = parse_hotels(resp)
    # 도심권만 (district != Other) — Disney 같은 외곽 호텔 제외
    all_hotels = [h for h in all_hotels if h.district != "Other"]
    hotels = [h for h in all_hotels if h.review_score >= 8.0]
    print(f"도심권 + 평점 ≥8.0: {len(hotels)}")
    if len(hotels) < 7:
        hotels = [h for h in all_hotels if h.review_score >= 7.5]
        print(f"  → 7.5 완화: {len(hotels)}")

    if not hotels:
        print("[ERROR] 조건 맞는 호텔 없음.")
        sys.exit(1)

    # 점수 산정 → top 7
    prices = [h.price_per_night for h in hotels]
    p_lo, p_hi = min(prices), max(prices)
    hotels_scored = sorted(hotels, key=lambda h: score_hotel(h, p_lo, p_hi), reverse=True)
    top7 = hotels_scored[:7]
    print("\nTop 7 호텔:")
    for i, h in enumerate(top7, 1):
        print(f"  {i}. {h.name[:30]:30s} {h.review_score:.1f} {h.district:<15s} ₩{h.price_per_night:>7,}/박")

    # 지도
    map_png = RESULTS_DIR / "hotels_map.png"
    render_map(top7, map_png)
    print(f"\n지도 → {map_png}")

    # PPT
    pptx_path = RESULTS_DIR / "trip_report.pptx"
    build_pptx(trips, top7, map_png, budget_per_pax=1_500_000, out_pptx=pptx_path)
    print(f"PPT → {pptx_path}")


if __name__ == "__main__":
    main()
