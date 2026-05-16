"""Top 10 일정 후보의 상세 markdown + 호텔 링크 보고서.

results.csv + cache.db만 사용 (SerpAPI 추가 호출 0건).
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
CACHE_DB = ROOT / "cache.db"

WD = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
SCEN = {
    "day_trip": "마카오 당일치기 (HKG↔HKG, 페리 왕복)",
    "overnight_rt": "마카오 1박 (HKG↔HKG, 페리 왕복)",
    "overnight_mfm_out": "마카오 1박 (ICN→HKG / MFM→ICN, 페리 편도)",
}


def fmt_date(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    return f"{d:%Y-%m-%d}({WD[d.weekday()]})"


def fmt_time(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    return f"{d:%m/%d %H:%M}"


def fmt_dur(min_total: int) -> str:
    h, m = divmod(min_total, 60)
    return f"{h}h{m:02d}m"


# 공항 UTC offset (단순 하드코딩)
_TZ_OFFSET_HRS = {
    "ICN": 9, "GMP": 9,
    "HKG": 8, "MFM": 8,
}


def real_duration_min(depart_iso: str, arrive_iso: str, from_iata: str, to_iata: str) -> int:
    """공항별 UTC offset을 적용한 실제 비행 소요(분).

    SerpAPI는 양쪽 공항의 local time을 그대로 줘서, 단순 (arrive-depart)는
    시차를 반영하지 못함 (예: ICN 06:35 → HKG 20:20 = 표면상 13h45m이지만
    실제 14h45m).
    """
    dep = datetime.fromisoformat(depart_iso)
    arr = datetime.fromisoformat(arrive_iso)
    tz_from = _TZ_OFFSET_HRS.get(from_iata, 9)
    tz_to = _TZ_OFFSET_HRS.get(to_iata, 8)
    apparent = (arr - dep).total_seconds() // 60
    # 실제 elapsed = apparent + (depart_tz - arrive_tz)
    return int(apparent + (tz_from - tz_to) * 60)


def load_hotel_index() -> Dict:
    """cache.db의 모든 호텔 응답 → {(city, checkin, checkout): {name: property_dict}}."""
    con = sqlite3.connect(CACHE_DB)
    rows = con.execute("SELECT key, value FROM cache WHERE key LIKE 'serpapi:%'").fetchall()
    con.close()

    index: Dict = {}
    for key, val in rows:
        if "engine=google_hotels" not in key:
            continue
        params = dict(p.split("=", 1) for p in key.replace("serpapi:", "").split("&") if "=" in p)
        q = params.get("q", "").lower().replace("+", " ").replace("%20", " ")
        city = "Macau" if "macau" in q else "Hong Kong"
        checkin = params.get("check_in_date")
        checkout = params.get("check_out_date")
        if not checkin or not checkout:
            continue
        data = json.loads(val)
        props = data.get("properties") or []
        name_map = {p.get("name", ""): p for p in props}
        index[(city, checkin, checkout)] = name_map
    return index


def find_hotel(index: Dict, city: str, checkin: str, checkout: str, name: str) -> Optional[Dict]:
    """이름으로 호텔 메타 조회 (좌표·링크·평점 등 포함)."""
    name_map = index.get((city, checkin, checkout)) or {}
    if name in name_map:
        return name_map[name]
    # fuzzy: prefix match
    for k, v in name_map.items():
        if k.startswith(name[:8]) or name.startswith(k[:8]):
            return v
    return None


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with open(RESULTS_DIR / "results.csv", encoding="utf-8") as f:
        trips = list(csv.DictReader(f))

    idx = load_hotel_index()

    # 고유 호텔 모음
    unique_hotels: OrderedDict = OrderedDict()  # name → meta_dict + city + rank
    for t in trips:
        for col, city in (("hk_hotel", "Hong Kong"), ("macau_hotel", "Macau")):
            nm = t.get(col)
            if not nm or nm in unique_hotels:
                continue
            # 마카오 1박 = (return-1, return)
            if city == "Macau":
                from datetime import date, timedelta
                ret = date.fromisoformat(t["return"])
                ci, co = (ret - timedelta(days=1)).isoformat(), ret.isoformat()
            else:
                ci, co = t["arrival"], t["return"]
            meta = find_hotel(idx, city, ci, co, nm)
            unique_hotels[nm] = {"city": city, "first_rank": t["rank"], "meta": meta}

    # ----- 보고서 작성 -----
    out = RESULTS_DIR / "candidates_detail.md"
    L = []
    L.append("# 홍콩 일정 후보 상세 (Top 10)\n")
    L.append("## 1인당 가격에 포함된 것")
    L.append("- 4인분 왕복 항공권 ÷ 4")
    L.append("- HK 호텔 (1박/객실 × 박수 × 객실수) ÷ 4인")
    L.append("- (마카오 1박 시나리오) 마카오 호텔 1박 × 객실수 ÷ 4인")
    L.append("- 페리·입국세 ₩50,000/인 (당일치기/페리 왕복) 또는 ₩28,000/인 (페리 편도)")
    L.append("- 환율·수수료 버퍼 3%")
    L.append("\n## 포함 안 된 것")
    L.append("식비, MTR/택시 등 시내 교통, 디즈니랜드/오션파크 입장료, 쇼핑\n")
    L.append("---\n")

    # 후보 요약 테이블
    L.append("## 한눈에 보기\n")
    L.append("| # | 일정 | 시나리오 | 객실 | HK 호텔 (1박/객실) | 마카오 호텔 | 1인 |")
    L.append("|---|---|---|---|---|---|---|")
    rooms = 2  # config
    for t in trips:
        scen_short = {"day_trip": "당일치기", "overnight_rt": "1박-왕복", "overnight_mfm_out": "1박-MFM귀국"}[t["scenario"]]
        hk = t["hk_hotel"]
        hk_price = int(t["hk_total_krw"]) // (int(t["hk_nights"]) * rooms)
        mc = t["macau_hotel"] or "-"
        if mc != "-":
            mc_price = int(t["macau_total_krw"]) // (int(t["macau_nights"]) * rooms) if int(t["macau_nights"]) > 0 else 0
            mc_str = f"{mc[:14]} ₩{mc_price:,}"
        else:
            mc_str = "-"
        L.append(
            f"| {t['rank']} | {fmt_date(t['arrival'])}→{fmt_date(t['return'])} | {scen_short} | "
            f"{rooms} | {hk[:18]} ₩{hk_price:,} | {mc_str} | ₩{int(t['cost_per_pax_krw']):,} |"
        )
    L.append("")

    # 상세
    L.append("\n---\n## 후보별 상세\n")
    for t in trips:
        rk = t["rank"]
        scen = SCEN.get(t["scenario"], t["scenario"])
        L.append(f"### Rank {rk}  ·  1인 ₩{int(t['cost_per_pax_krw']):,}  ·  점수 {t['score']}")
        L.append(f"- **시나리오**: {scen}")
        L.append(f"- **일정**: {fmt_date(t['arrival'])} 도착 → {fmt_date(t['return'])} 귀국 · {int(t['hk_nights']) + int(t['macau_nights'])}박")
        L.append("")
        L.append("**✈ 항공편**")
        L.append("")
        L.append("| 구간 | 항공사 | 편명 | 출발 | 도착 | 소요 | 경유 |")
        L.append("|---|---|---|---|---|---|---|")
        out_dur = real_duration_min(t['out_depart'], t['out_arrive'], t['out_from'], t['out_to'])
        in_dur = real_duration_min(t['in_depart'], t['in_arrive'], t['in_from'], t['in_to'])
        L.append(
            f"| 출국 | {t['out_carrier']} | {t['out_no']} | {fmt_time(t['out_depart'])} ({t['out_from']} 현지) "
            f"| {fmt_time(t['out_arrive'])} ({t['out_to']} 현지) | "
            f"{fmt_dur(out_dur)} | "
            f"{'직항' if t['out_stops'] == '0' else t['out_stops'] + '경유'} |"
        )
        L.append(
            f"| 귀국 | {t['in_carrier']} | {t['in_no']} | {fmt_time(t['in_depart'])} ({t['in_from']} 현지) "
            f"| {fmt_time(t['in_arrive'])} ({t['in_to']} 현지) | "
            f"{fmt_dur(in_dur)} | "
            f"{'직항' if t['in_stops'] == '0' else t['in_stops'] + '경유'} |"
        )
        L.append("")
        L.append(f"- 항공 합계: ₩{int(t['flight_total_krw']):,} (4인) → 1인 ₩{int(t['flight_total_krw']) // 4:,}")
        L.append("")
        L.append("**🏨 숙소**")
        # HK
        hk_name = t["hk_hotel"]
        hk_info = unique_hotels.get(hk_name) or {}
        hk_meta = hk_info.get("meta") or {}
        hk_link = hk_meta.get("link", "")
        hk_gps = hk_meta.get("gps_coordinates") or {}
        hk_price = int(t["hk_total_krw"]) // (int(t["hk_nights"]) * rooms)
        L.append(f"- **HK** · {hk_name}" + (f" — [Google Hotels 링크]({hk_link})" if hk_link else ""))
        loc = f"{t['hk_district']}"
        if hk_gps.get("latitude"):
            loc += f" · 좌표 {hk_gps['latitude']:.4f}, {hk_gps['longitude']:.4f}"
        L.append(f"  - 위치: {loc}")
        L.append(f"  - 1박 / 객실: ₩{hk_price:,}  ·  객실 **{rooms}개** × {t['hk_nights']}박 = ₩{int(t['hk_total_krw']):,}")
        if hk_meta.get("overall_rating"):
            score10 = float(hk_meta["overall_rating"]) * 2
            L.append(f"  - 평점: {score10:.1f}/10 (Google {hk_meta['overall_rating']}/5)")
        # Macau
        if t["macau_hotel"]:
            mc_name = t["macau_hotel"]
            mc_info = unique_hotels.get(mc_name) or {}
            mc_meta = mc_info.get("meta") or {}
            mc_link = mc_meta.get("link", "")
            mc_gps = mc_meta.get("gps_coordinates") or {}
            mc_price = int(t["macau_total_krw"]) // (int(t["macau_nights"]) * rooms)
            L.append(f"- **마카오** · {mc_name}" + (f" — [Google Hotels 링크]({mc_link})" if mc_link else ""))
            loc = "Macau"
            if mc_gps.get("latitude"):
                loc += f" · 좌표 {mc_gps['latitude']:.4f}, {mc_gps['longitude']:.4f}"
            L.append(f"  - 위치: {loc}")
            L.append(f"  - 1박 / 객실: ₩{mc_price:,}  ·  객실 **{rooms}개** × {t['macau_nights']}박 = ₩{int(t['macau_total_krw']):,}")
            if mc_meta.get("overall_rating"):
                score10 = float(mc_meta["overall_rating"]) * 2
                L.append(f"  - 평점: {score10:.1f}/10 (Google {mc_meta['overall_rating']}/5)")
        L.append("")
        L.append(f"**🚢 페리·기타**: ₩{int(t['side_trip_per_pax_krw']):,}/인 × 4 = ₩{int(t['side_trip_per_pax_krw']) * 4:,}")
        L.append("")
        # 비용 내역 정합성 (단순 합산)
        flight_per_pax = int(t['flight_total_krw']) // 4
        hk_per_pax = int(t['hk_total_krw']) // 4
        mc_per_pax = int(t['macau_total_krw']) // 4 if t['macau_total_krw'] else 0
        ferry_per_pax = int(t['side_trip_per_pax_krw'])
        subtotal = flight_per_pax + hk_per_pax + mc_per_pax + ferry_per_pax
        total = int(t['cost_per_pax_krw'])
        L.append("**1인 비용 분해**")
        L.append("")
        L.append("| 항목 | 1인 ₩ |")
        L.append("|---|---|")
        L.append(f"| 항공 | {flight_per_pax:,} |")
        L.append(f"| HK 호텔 | {hk_per_pax:,} |")
        if mc_per_pax:
            L.append(f"| 마카오 호텔 | {mc_per_pax:,} |")
        L.append(f"| 페리·세금 | {ferry_per_pax:,} |")
        L.append(f"| 소계 | {subtotal:,} |")
        L.append(f"| **+ 환율버퍼 3%** | **{total - subtotal:,}** |")
        L.append(f"| **= 1인 총계** | **{total:,}** |")
        L.append("")
        L.append("---\n")

    out.write_text("\n".join(L), encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
