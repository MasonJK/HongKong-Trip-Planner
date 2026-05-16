"""호텔 × 시나리오 (4박 HK vs 3박+1박 마카오) 비용 표."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 항공/페리/구성 고정
FLIGHT_TOTAL_PAX = 504_244     # YP 801 + TW 644 직항 4인분÷4
FERRY_DAY_TRIP_PAX = 50_000    # 마카오 당일치기 페리 왕복 + 입국세
FERRY_RT_PAX = 50_000          # 1박 시나리오도 페리 왕복 (동일가)
FX_BUFFER = 0.03
ROOMS = 2
PAX = 4
MC_BELIVE = 132_127            # Belive Andmore (가장 싼 마카오 옵션, 8.6 평점)

HOTELS = [
    ("빅토리아 호텔 (참고)",         153_179, "TST 1성 8.8/91리뷰 — 사용자: 컨디션 안 좋음"),
    ("예스인 YMT (호스텔/참고)",      146_090, "YMT 2성 8.0/621 — 도미토리 혼합 호스텔, 가족 부적합"),
    ("차터하우스 CWB",              229_488, "CWB 3성 7.2/1533 — 헬스장24h·식당2·스포츠바·룸서비스"),
    ("판다 호텔 (Tsuen Wan)",       236_598, "Tsuen Wan 4성 8.6/10,363 — 야외수영장·헬스장·식당3 (외곽 MTR 종점)"),
    ("베스트 웨스턴 플러스 (Sheung Wan)", 256_607, "Sheung Wan 4성 7.2/1886 — 무료 조식·헬스장 (Central 도보)"),
    ("아이클럽 셩완 (참고)",          306_344, "Sheung Wan 4성 8.0/1567 — 사용자: 너무 비쌈"),
]


def trip_cost_4night_hk(hotel_per_night: int) -> dict:
    hk_total = hotel_per_night * ROOMS * 4   # 4인 호텔비 합산
    hk_per_pax = hk_total // PAX
    subtotal = FLIGHT_TOTAL_PAX + hk_per_pax + FERRY_DAY_TRIP_PAX
    total = int(subtotal * (1 + FX_BUFFER))
    return {
        "flight": FLIGHT_TOTAL_PAX,
        "hotel": hk_per_pax,
        "macau_hotel": 0,
        "ferry": FERRY_DAY_TRIP_PAX,
        "subtotal": subtotal,
        "buffer": total - subtotal,
        "total": total,
        "4인_total": total * PAX,
    }


def trip_cost_3night_hk_1night_mc(hotel_per_night: int, mc_per_night: int = MC_BELIVE) -> dict:
    hk_total = hotel_per_night * ROOMS * 3
    mc_total = mc_per_night * ROOMS * 1
    hk_per_pax = hk_total // PAX
    mc_per_pax = mc_total // PAX
    subtotal = FLIGHT_TOTAL_PAX + hk_per_pax + mc_per_pax + FERRY_RT_PAX
    total = int(subtotal * (1 + FX_BUFFER))
    return {
        "flight": FLIGHT_TOTAL_PAX,
        "hotel": hk_per_pax,
        "macau_hotel": mc_per_pax,
        "ferry": FERRY_RT_PAX,
        "subtotal": subtotal,
        "buffer": total - subtotal,
        "total": total,
        "4인_total": total * PAX,
    }


print(f"{'호텔':<40} {'4박HK(1인)':<15} {'3박+1박MC(1인)':<18} {'절감(1인)':<12} {'4인 절감'}")
print("-" * 105)
for name, price, _note in HOTELS:
    a = trip_cost_4night_hk(price)
    b = trip_cost_3night_hk_1night_mc(price)
    save_pax = a["total"] - b["total"]
    save_4 = save_pax * PAX
    print(f"{name[:38]:<40} ₩{a['total']:>9,}     ₩{b['total']:>9,}        "
          f"₩{save_pax:>7,}     ₩{save_4:>9,}")

print(f"\n공통: 항공 ₩{FLIGHT_TOTAL_PAX:,}/인 (YP+TW 직항) · 마카오 호텔 Belive Andmore ₩{MC_BELIVE:,}/박 · 페리 ₩{FERRY_DAY_TRIP_PAX:,}/인 · 환율버퍼 {FX_BUFFER:.0%}")
print(f"전제: 객실 {ROOMS}개 · {PAX}인 · 10/11→10/15 일정 가정")
