"""검색 어댑터 추상 기반. Mock·Playwright·Amadeus 등으로 갈아끼울 수 있게."""
from abc import ABC, abstractmethod
from datetime import date
from typing import List

from ..models import FlightItinerary, Hotel


class FlightAdapter(ABC):
    @abstractmethod
    def search(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        pax: int,
        max_stops: int,
    ) -> List[FlightItinerary]:
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
