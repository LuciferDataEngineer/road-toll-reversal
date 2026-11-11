"""Target-path arithmetic from the National Road Safety Strategy rules.

The 2030 goal is half the 2018–2020 annual death toll. The path used here is the
straight line agencies already use to judge progress: full baseline at the end of
2020, half of it at the end of 2030.
"""

from __future__ import annotations

import calendar

from road_toll.paths import DEATHS_REDUCTION, HORIZON_YEAR


def trajectory_factor(year: int, month: int, *, reduction: float = DEATHS_REDUCTION) -> float:
    """Share of the baseline still allowed in the 12 months ending this month."""
    elapsed = (year - 2020) * 12 + month - 12
    span = (HORIZON_YEAR - 2020) * 12
    elapsed = min(span, max(0, elapsed))
    return 1.0 - reduction * (elapsed / span)


def mean_year_end_factor(years: tuple[int, ...] | list[int]) -> float:
    if not years:
        raise ValueError("years must not be empty")
    return sum(trajectory_factor(year, 12) for year in years) / len(years)


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def rolling_sum(monthly: list[int], window: int = 12) -> list[float | None]:
    """Rolling sum aligned to the right edge. Early months are None."""
    totals: list[float | None] = []
    running = 0
    for index, value in enumerate(monthly):
        running += value
        if index >= window:
            running -= monthly[index - window]
        totals.append(float(running) if index >= window - 1 else None)
    return totals


def round_people(value: float) -> int:
    return int(round(value))
