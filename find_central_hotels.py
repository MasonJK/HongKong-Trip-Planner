"""Central/Sheung Wan/Wan Chai/Admiralty 권에서 중간 가격대 옵션 탐색.

타겟:
  - 가격: ₩170,000 ~ ₩280,000 / 박 / 객실 (빅토리아 ↔ 아이클럽 사이)
  - 평점: ≥ 8.0
  - 리뷰 수: ≥ 100 (표본 안정성)
  - 별: ≥ 4.0 (호텔 컨디션 보장)

캐시 추가 호출 없음 — 기존 SerpAPI 응답에서 모두 추출.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
CACHE_DB = ROOT / "cache.db"

# 타겟 지역 중심
CENTRAL_AREAS = {
    "Central":       (22.2820, 114.1582),
    "Sheung Wan":    (22.2873, 114.1502),
    "Wan Chai":      (22.2774, 114.1716),
    "Admiralty":     (22.2796, 114.1647),
    "Mid-Levels":    (22.2795, 114.1530),
    "Soho":          (22.2832, 114.1530),
}

# 필터 기준
PRICE_MIN = 170_000
PRICE_MAX = 290_000
SCORE10_MIN = 8.0
REVIEWS_MIN = 100
STAR_MIN = 4.0
AREA_RADIUS_KM = 1.5


@dataclass
class Candidate:
    name: str
    type: str
    star: float
    score10: float
    reviews: int
    location_rating: float
    district: str
    distance_km: float
    lat: float
    lon: float
    prices: List[int] = field(default_factory=list)
    refundable: bool = False
    link: str = ""
    description: str = ""
    amenities: List[str] = field(default_factory=list)
    check_in: str = ""
    check_out: str = ""

    @property
    def median_price(self) -> int:
        if not self.prices:
            return 0
        s = sorted(self.prices)
        return s[len(s) // 2]


def closest_central(lat: float, lon: float) -> Tuple[str, float]:
    """가장 가까운 Central권 지역과 거리(km)."""
    cos_lat = math.cos(math.radians(lat))
    best, bd = "Other", float("inf")
    for name, (cl, clo) in CENTRAL_AREAS.items():
        d = math.hypot((lon - clo) * cos_lat * 111, (lat - cl) * 111)
        if d < bd:
            best, bd = name, d
    return best, bd


def parse_property(p: Dict) -> Optional[Candidate]:
    """SerpAPI Property → Candidate (Central권 1.5km 이내만)."""
    gps = p.get("gps_coordinates") or {}
    lat, lon = gps.get("latitude"), gps.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None

    district, dist = closest_central(lat, lon)
    if dist > AREA_RADIUS_KM:
        return None

    # 가격
    rpn = p.get("rate_per_night") or {}
    per_night = int(rpn.get("extracted_lowest") or 0)
    if per_night == 0:
        return None

    # 평점
    raw_rating = float(p.get("overall_rating") or 0)
    score10 = raw_rating * 2 if 0 < raw_rating <= 5 else raw_rating
    reviews = int(p.get("reviews") or 0)
    loc_rating = float(p.get("location_rating") or 0)

    # 별
    hc = p.get("extracted_hotel_class") or p.get("hotel_class") or 0
    try:
        star = float(hc) if hc else 0.0
    except (TypeError, ValueError):
        star = 0.0

    return Candidate(
        name=p.get("name", "Unknown"),
        type=p.get("type", "hotel"),
        star=star,
        score10=round(score10, 1),
        reviews=reviews,
        location_rating=loc_rating,
        district=district,
        distance_km=round(dist, 2),
        lat=float(lat), lon=float(lon),
        prices=[per_night],
        refundable=bool(p.get("free_cancellation")),
        link=p.get("link", ""),
        description=p.get("description", ""),
        amenities=p.get("amenities") or [],
        check_in=p.get("check_in_time", ""),
        check_out=p.get("check_out_time", ""),
    )


def collect_all_hk_props():
    """모든 HK google_hotels 응답의 properties를 yield."""
    con = sqlite3.connect(CACHE_DB)
    rows = con.execute("SELECT key, value FROM cache WHERE key LIKE 'serpapi:%'").fetchall()
    con.close()
    for key, val in rows:
        if "engine=google_hotels" not in key:
            continue
        if "macau" in key.lower():
            continue
        data = json.loads(val)
        for p in data.get("properties") or []:
            yield p


def merge_by_name(cands: List[Candidate]) -> List[Candidate]:
    """이름이 같은 후보를 합쳐 가격 분포 보존."""
    by_name: Dict[str, Candidate] = {}
    for c in cands:
        if c.name in by_name:
            by_name[c.name].prices.extend(c.prices)
            # 메타데이터 보강 (빈 필드 채우기)
            existing = by_name[c.name]
            if not existing.link and c.link:
                existing.link = c.link
            if not existing.description and c.description:
                existing.description = c.description
            if not existing.amenities and c.amenities:
                existing.amenities = c.amenities
        else:
            by_name[c.name] = c
    return list(by_name.values())


def quality_score(c: Candidate) -> float:
    """가성비·품질·표본안정성 종합 점수.

    가중:
      - 가격 가성비: 25% (싸면 좋음, ₩170k=1.0, ₩290k=0.0)
      - 평점: 30%
      - 리뷰 수 안정성: 20%
      - 위치 평점: 15%
      - 별: 10%
    """
    price = c.median_price
    s_price = max(0.0, min(1.0, (PRICE_MAX - price) / (PRICE_MAX - PRICE_MIN)))
    s_score = max(0.0, min(1.0, (c.score10 - 7.5) / 2.0))  # 7.5=0, 9.5=1
    s_reviews = min(1.0, math.log(max(c.reviews, 1)) / math.log(2000))
    s_loc = max(0.0, min(1.0, (c.location_rating - 4.0) / 1.0))  # 4.0=0, 5.0=1
    s_star = max(0.0, min(1.0, (c.star - 3.5) / 1.5))  # 3.5=0, 5.0=1

    return round(0.25 * s_price + 0.30 * s_score + 0.20 * s_reviews
                 + 0.15 * s_loc + 0.10 * s_star, 3)


# ---- Main -----------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    raw = []
    for p in collect_all_hk_props():
        c = parse_property(p)
        if c:
            raw.append(c)
    merged = merge_by_name(raw)

    print(f"Central권 1.5km 이내 호텔: {len(merged)}곳\n")

    # 필터링
    qualified = [
        c for c in merged
        if PRICE_MIN <= c.median_price <= PRICE_MAX
        and c.score10 >= SCORE10_MIN
        and c.reviews >= REVIEWS_MIN
        and c.star >= STAR_MIN
    ]
    print(f"가격 {PRICE_MIN/1000:.0f}k~{PRICE_MAX/1000:.0f}k + 평점 ≥{SCORE10_MIN} + 리뷰 ≥{REVIEWS_MIN} + 별 ≥{STAR_MIN}: {len(qualified)}곳\n")

    qualified.sort(key=quality_score, reverse=True)

    print(f"{'순위':<4} {'호텔명':<32} {'지역':<11} {'1박':<11} {'평점':<5} {'리뷰':<7} {'별':<4} {'위치':<5} {'종합'}")
    print("-" * 100)
    for i, c in enumerate(qualified[:15], 1):
        print(f"{i:<4} {c.name[:30]:<32} {c.district:<11} ₩{c.median_price:<10,} "
              f"{c.score10:<5} {c.reviews:<7} {c.star:<4.1f} {c.location_rating:<5.1f} {quality_score(c):.3f}")

    # 참고 — 빅토리아·아이클럽 비교 라인
    print("\n[참고] 기존 비교 대상:")
    for name in ("빅토리아 호텔", "아이클럽 셩완 호텔"):
        for c in merged:
            if name in c.name:
                print(f"  - {c.name}  ₩{c.median_price:,}/박  평점 {c.score10}  리뷰 {c.reviews}  "
                      f"별 {c.star:.1f}★  위치 {c.location_rating}  종합 {quality_score(c):.3f}")
                break

    # 후보 저장 (다음 단계 비교/지도용)
    out = []
    for c in qualified[:6]:
        out.append({
            "name": c.name, "district": c.district, "lat": c.lat, "lon": c.lon,
            "median_price": c.median_price, "score10": c.score10, "reviews": c.reviews,
            "star": c.star, "location_rating": c.location_rating,
            "type": c.type, "refundable": c.refundable, "link": c.link,
            "description": c.description, "amenities": c.amenities,
            "check_in": c.check_in, "check_out": c.check_out,
            "distance_km": c.distance_km, "quality": quality_score(c),
        })
    (RESULTS_DIR / "central_candidates.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n→ results/central_candidates.json (Top 6 저장)")


if __name__ == "__main__":
    main()
