"""CLI 진입점.

사용 예:
    python -m trip_optimizer.main --config trip_optimizer/config.yaml --out results
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

from .adapters.mock import MockFlightAdapter, MockHotelAdapter
from .optimizer import optimize
from .report import render_markdown, write_csv


def _coerce_dates(cfg: dict) -> dict:
    """YAML에서 date로 파싱된 값을 date 객체로 통일."""
    dr = cfg["trip"]["date_range"]
    for k in ("start", "end"):
        if isinstance(dr[k], str):
            dr[k] = date.fromisoformat(dr[k])
    for ex in cfg.get("exclude_date_ranges", []) or []:
        for k in ("start", "end"):
            if isinstance(ex[k], str):
                ex[k] = date.fromisoformat(ex[k])
    return cfg


def load_adapters(name: str):
    if name == "mock":
        return MockFlightAdapter(), MockHotelAdapter()
    if name == "serpapi":
        from .adapters.serpapi import SerpApiFlightAdapter, SerpApiHotelAdapter
        return SerpApiFlightAdapter(), SerpApiHotelAdapter()
    raise ValueError(f"Unknown adapter: {name}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="홍콩 여행 최적화")
    p.add_argument("--config", default="trip_optimizer/config.yaml")
    p.add_argument("--out", default="results", help="출력 디렉토리")
    args = p.parse_args(argv)

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg = _coerce_dates(cfg)

    flight_adapter, hotel_adapter = load_adapters(cfg.get("adapter", "mock"))
    candidates = optimize(cfg, flight_adapter, hotel_adapter)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "results.md"
    csv_path = out_dir / "results.csv"

    md = render_markdown(candidates, cfg["trip"]["budget_per_person_krw"])
    md_path.write_text(md, encoding="utf-8")
    write_csv(candidates, csv_path)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"✓ {len(candidates)}개 후보 → {md_path}, {csv_path}")
    if candidates:
        top = candidates[0]
        print(
            f"  최상위: {top.arrival} ~ {top.return_date}, "
            f"1인 ₩{top.cost_per_person_krw:,}, 점수 {top.score:.3f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
