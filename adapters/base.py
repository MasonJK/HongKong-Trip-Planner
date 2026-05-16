"""검색 어댑터 추상 기반. Mock·SerpAPI·Playwright 등으로 갈아끼울 수 있게."""
from abc import ABC, abstractmethod
from datetime import date
from typing import List

from ..models import Flight, Hotel


class FlightAdapter(ABC):
    """편도 단일 leg 검색. 옵티마이저가 outbound × inbound로 조합."""

    @abstractmethod
    def search_oneway(
        self,
        origin: str,
        destination: str,
        when: date,
        pax: int,
        max_stops: int,
    ) -> List[Flight]:
        ...


class HotelAdapter(ABC):
    @abstractmethod
    def search(
        self,
        city: str,
        checkin: date,
        checkout: date,
        rooms: int,
        pax: int,
    ) -> List[Hotel]:
        ...
