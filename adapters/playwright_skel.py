"""Playwright 기반 스크래퍼 스켈레톤.

실제 운영하려면:
  pip install playwright
  python -m playwright install chromium

각 사이트는 셀렉터·HTML 구조가 자주 바뀌므로, 이 파일은 '뼈대'만 제공합니다.
- Skyscanner / 네이버항공 등에서 실제 셀렉터를 채워 넣으세요.
- 반복 호출 시 IP 차단 가능 — 캐시·딜레이·user-agent rotation 필수.
"""
from __future__ import annotations

from datetime import date
from typing import List

from ..models import Flight, Hotel
from .base import FlightAdapter, HotelAdapter


class PlaywrightFlightAdapter(FlightAdapter):
    def __init__(self, headless: bool = True, delay_sec: float = 2.0):
        self.headless = headless
        self.delay_sec = delay_sec

    def search_oneway(self, origin, destination, when, pax, max_stops) -> List[Flight]:
        # from playwright.sync_api import sync_playwright
        # with sync_playwright() as p:
        #     browser = p.chromium.launch(headless=self.headless)
        #     page = browser.new_page()
        #     url = (
        #         f"https://www.skyscanner.co.kr/transport/flights/"
        #         f"{origin.lower()}/{destination.lower()}/"
        #         f"{depart:%y%m%d}/{return_:%y%m%d}/"
        #         f"?adults={pax}&cabinclass=economy"
        #     )
        #     page.goto(url, timeout=60000)
        #     page.wait_for_selector('[data-testid="result-card"]', timeout=30000)
        #     cards = page.query_selector_all('[data-testid="result-card"]')
        #     itineraries = []
        #     for card in cards[:20]:
        #         # 셀렉터는 사이트 구조 변경 시 갱신 필요
        #         carrier = card.query_selector('.carrier-name').inner_text()
        #         price_text = card.query_selector('.price').inner_text()
        #         ... # 파싱 → Flight 객체 생성
        #     browser.close()
        #     return itineraries
        raise NotImplementedError(
            "Playwright 어댑터는 사이트별 셀렉터를 직접 채워넣어야 합니다. "
            "우선 config.yaml에서 adapter: mock 으로 동작 확인 후 작업하세요."
        )


class PlaywrightHotelAdapter(HotelAdapter):
    def __init__(self, headless: bool = True, delay_sec: float = 2.0):
        self.headless = headless
        self.delay_sec = delay_sec

    def search(self, city, checkin, checkout, rooms, pax) -> List[Hotel]:
        # Agoda/Booking 검색 URL 패턴 활용
        # https://www.agoda.com/search?city=18175&checkIn=2026-10-18&checkOut=2026-10-22&rooms=2&adults=4
        raise NotImplementedError(
            "Playwright 어댑터는 사이트별 셀렉터를 직접 채워넣어야 합니다."
        )
