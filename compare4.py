"""4 호텔 (빅토리아, 차터하우스 CWB, 베스트 웨스턴 플러스, 아이클럽 셩완) 비교."""
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from staticmap import CircleMarker, StaticMap

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from compare_hotels import all_hotel_responses

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"


SPEC = [
    {"label": "빅토리아 호텔 (참고)",        "needles": ["빅토리아 호텔", "Victoria"],
     "color": "#E74C3C", "tag": "참고"},
    {"label": "차터하우스 코즈웨이 베이",     "needles": ["차터하우스 코즈웨이", "Charterhouse"],
     "color": "#2ECC71", "tag": "추천 A"},
    {"label": "베스트 웨스턴 플러스 홍콩",    "needles": ["베스트 웨스턴 플러스", "Best Western Plus"],
     "color": "#F39C12", "tag": "추천 B"},
    {"label": "아이클럽 셩완 (참고)",         "needles": ["아이클럽 셩완 호텔"],
     "color": "#3498DB", "tag": "참고"},
]


def find_first(needles):
    """캐시에서 needles 매칭되는 첫 호텔."""
    for resp, ci, co in all_hotel_responses():
        for p in resp.get("properties") or []:
            name = p.get("name", "")
            if any(n in name for n in needles):
                return p, ci, co
    return None, None, None


def find_for_target_dates(needles, target_ci="2026-10-11"):
    """타깃 일자 또는 가까운 날짜에서 우선 매칭 (가격 일관성)."""
    found_target = found_any = None
    for resp, ci, co in all_hotel_responses():
        for p in resp.get("properties") or []:
            name = p.get("name", "")
            if any(n in name for n in needles):
                rate = (p.get("rate_per_night") or {}).get("extracted_lowest")
                if not rate:
                    continue
                if ci == target_ci:
                    return p, ci, co
                if not found_any:
                    found_any = (p, ci, co)
    return found_any if found_any else (None, None, None)


def font(size):
    for path in ["C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/Arial.ttf"]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def krw(n):
    return f"₩{n:,}"


def render_4(hotels, out_png, width=2200, height=1500):
    coords = [(h["lon"], h["lat"]) for h in hotels]
    lats = [c[1] for c in coords]
    lons = [c[0] for c in coords]
    lat_pad = max(0.005, (max(lats) - min(lats)) * 0.35)
    lon_pad = max(0.008, (max(lons) - min(lons)) * 0.35)
    bbox = (min(lons) - lon_pad, min(lats) - lat_pad,
            max(lons) + lon_pad, max(lats) + lat_pad)

    m = StaticMap(width, height, padding_x=80, padding_y=80,
                  url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
                  headers={"User-Agent": "trip-optimizer/0.3"})
    m.add_marker(CircleMarker((bbox[0], bbox[1]), "#00000000", 1))
    m.add_marker(CircleMarker((bbox[2], bbox[3]), "#00000000", 1))
    for h in hotels:
        m.add_marker(CircleMarker((h["lon"], h["lat"]), "white", 62))
        m.add_marker(CircleMarker((h["lon"], h["lat"]), h["color"], 52))
    img = m.render()
    draw = ImageDraw.Draw(img)

    font_num = font(48)
    font_title = font(26)
    font_body = font(20)
    font_tag = font(18)

    def lonlat_to_xy(lon, lat):
        zoom = m.zoom
        x = (lon + 180.0) / 360.0 * (2 ** zoom) * 256
        sin_lat = math.sin(math.radians(lat))
        y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * (2 ** zoom) * 256
        px = x - (m.x_center * 256 - width / 2)
        py = y - (m.y_center * 256 - height / 2)
        return int(px), int(py)

    positions = [lonlat_to_xy(h["lon"], h["lat"]) for h in hotels]

    # 마커 번호
    for i, (px, py) in enumerate(positions):
        label = str(i + 1)
        tb = draw.textbbox((0, 0), label, font=font_num)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        draw.text((px - tw // 2, py - th // 2 - 6), label, fill="white", font=font_num)

    # 정보 박스 — 4개를 화면 네 모서리에 가깝게
    corners = [
        (60, 60),                              # 좌상
        (width - 60, 60),                      # 우상
        (60, height - 60),                     # 좌하
        (width - 60, height - 60),             # 우하
    ]
    anchor_mode = ["TL", "TR", "BL", "BR"]

    for i, h in enumerate(hotels):
        meta = h["meta"]
        px, py = positions[i]
        color = h["color"]
        rr, gg, bb = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)

        amenities = meta.get("amenities") or []
        am_text = " · ".join(amenities[:5])

        lines = [
            (f"#{i+1}  {h['label']}", font_title, "title"),
            (f"{krw(h['price_per_night'])} / 박 / 객실", font_body, "price"),
            (f"별 {h['star']:.0f}★  ·  평점 {h['score10']:.1f}/10  ·  리뷰 {meta.get('reviews', 0):,}", font_body, "norm"),
            (f"위치: {h['district']} (위치평점 {meta.get('location_rating','-')}/5)", font_body, "norm"),
            (f"체크인 {meta.get('check_in_time','-')} / 체크아웃 {meta.get('check_out_time','-')}", font_body, "norm"),
            (f"편의: {am_text[:60]}", font_body, "norm"),
        ]
        desc = meta.get("description", "")
        if desc:
            lines.append((f"설명: {desc[:65]}", font_body, "norm"))

        # 박스 크기
        widths, heights = [], []
        for text, fnt, _ in lines:
            tb = draw.textbbox((0, 0), text, font=fnt)
            widths.append(tb[2] - tb[0])
            heights.append(tb[3] - tb[1])
        box_w = max(widths) + 36 + 50  # +50 for tag area
        box_h = sum(heights) + 28 + 12 * (len(lines) - 1)

        # 위치
        cx, cy = corners[i]
        mode = anchor_mode[i]
        if "R" in mode:
            x0 = cx - box_w
        else:
            x0 = cx
        if "B" in mode:
            y0 = cy - box_h
        else:
            y0 = cy

        # 박스
        draw.rectangle((x0, y0, x0 + box_w, y0 + box_h),
                       fill="white", outline="black", width=4)
        draw.rectangle((x0, y0, x0 + 14, y0 + box_h), fill=(rr, gg, bb))

        # 태그 (참고/추천)
        tag = h["tag"]
        tag_color = (rr, gg, bb)
        tag_tb = draw.textbbox((0, 0), tag, font=font_tag)
        tag_w = tag_tb[2] - tag_tb[0] + 16
        tag_h = tag_tb[3] - tag_tb[1] + 8
        draw.rectangle((x0 + box_w - tag_w - 8, y0 + 8,
                        x0 + box_w - 8, y0 + 8 + tag_h),
                       fill=tag_color)
        draw.text((x0 + box_w - tag_w, y0 + 12), tag, fill="white", font=font_tag)

        # 텍스트
        ty = y0 + 18
        for (text, fnt, kind), h_line in zip(lines, heights):
            if kind == "title":
                fill = (rr, gg, bb)
            elif kind == "price":
                fill = "black"
            else:
                fill = "black"
            draw.text((x0 + 24, ty), text, fill=fill, font=fnt)
            ty += h_line + 12

        # 마커 → 박스 모서리 leader line
        if "R" in mode and "T" in mode:
            anchor = (x0, y0 + box_h)
        elif "R" in mode and "B" in mode:
            anchor = (x0, y0)
        elif "L" in mode and "T" in mode:
            anchor = (x0 + box_w, y0 + box_h)
        else:  # LB
            anchor = (x0 + box_w, y0)
        draw.line([(px, py), anchor], fill=(rr, gg, bb), width=3)

    img.save(out_png)


def main():
    hotels = []
    for spec in SPEC:
        p, ci, co = find_for_target_dates(spec["needles"], target_ci="2026-10-11")
        if not p:
            p, ci, co = find_first(spec["needles"])
        if not p:
            print(f"[WARN] {spec['label']} not found")
            continue
        gps = p.get("gps_coordinates") or {}
        rpn = p.get("rate_per_night") or {}
        per_night = int(rpn.get("extracted_lowest") or 0)
        score = float(p.get("overall_rating") or 0)
        score10 = round(score * 2 if 0 < score <= 5 else score, 1)
        hc = p.get("extracted_hotel_class") or 0
        try:
            star = float(hc)
        except (TypeError, ValueError):
            star = 0.0
        hotels.append({
            "label": spec["label"],
            "color": spec["color"],
            "tag": spec["tag"],
            "meta": p,
            "lat": gps.get("latitude"),
            "lon": gps.get("longitude"),
            "price_per_night": per_night,
            "score10": score10,
            "star": star,
            "district": (
                {(22.297, 114.172): "TST",
                 (22.277, 114.179): "CWB/Wan Chai",
                 (22.287, 114.139): "Sheung Wan",
                 (22.286, 114.149): "Sheung Wan"}.get(
                    (round(gps.get("latitude", 0), 3), round(gps.get("longitude", 0), 3)),
                    "?"
                )
            ),
            "checkin_used": ci,
        })

    for h in hotels:
        print(f"{h['label']:<30} ₩{h['price_per_night']:>8,}  평점 {h['score10']}  별 {h['star']}  [{h['checkin_used']}]")

    out = RESULTS_DIR / "compare4_map.png"
    render_4(hotels, out)
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
