"""A per-person rate states the period it covers on its card (no database).

Water per capita is litres per person over the period shown: a month's value
reads "L/person/month", a year's "L/person/year". A year-to-date window gets no
suffix. The canonical unit and every other metric are unchanged.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.historical.resolver import Entry, Value, _aggregate_display, _display_item, _month_display

WATER_PER_CAPITA = "water_per_capita_l"


def _month() -> Entry:
    return Entry("2026-07", "Jul 2026", 2026, 7, "MONTHLY", date(2026, 7, 1), date(2026, 7, 31), "historical_verified")


def _year(granularity: str, key: str, end: date) -> Entry:
    return Entry(key, key, end.year, None, granularity, date(end.year, 1, 1), end, "historical_aggregate")


def _per_capita(amount: str, granularity: str) -> Value:
    return Value(WATER_PER_CAPITA, "water", "calculation", Decimal(amount), "L/person", granularity=granularity)


def test_a_months_value_is_per_person_per_month() -> None:
    entry = _month()
    entry.values[WATER_PER_CAPITA] = _per_capita("2927.659848", "MONTHLY")
    shown = _month_display(entry, [], {})[WATER_PER_CAPITA]
    assert shown["display_unit"] == "L/person/month"
    assert shown["unit"] == "L/person"  # canonical unit untouched
    assert shown["value"] == 2927.659848


def test_a_full_years_value_is_per_person_per_year() -> None:
    entry = _year("ANNUAL", "2025-FY", date(2025, 12, 31))
    entry.values[WATER_PER_CAPITA] = _per_capita("27994.278358", "ANNUAL")
    shown = _aggregate_display(entry, {})[WATER_PER_CAPITA]
    assert shown["display_unit"] == "L/person/year"
    assert shown["unit"] == "L/person"


def test_a_year_to_date_window_gets_no_period_suffix() -> None:
    entry = _year("YTD", "2026-YTD", date(2026, 7, 31))
    entry.values[WATER_PER_CAPITA] = _per_capita("20508.120441", "YTD")
    shown = _aggregate_display(entry, {})[WATER_PER_CAPITA]
    assert "display_unit" not in shown
    assert shown["unit"] == "L/person"


def test_an_annual_figure_shown_as_context_on_a_month_stays_per_year() -> None:
    # 2025 has no monthly water: a month shows the year's figure, labelled as such.
    year = _year("ANNUAL", "2025-FY", date(2025, 12, 31))
    year.values[WATER_PER_CAPITA] = _per_capita("27994.278358", "ANNUAL")
    month = Entry("2025-03", "Mar 2025", 2025, 3, "MONTHLY", date(2025, 3, 1), date(2025, 3, 31), "historical_verified")
    shown = _month_display(month, [year], {})[WATER_PER_CAPITA]
    assert shown["display_context"] is True
    assert shown["display_unit"] == "L/person/year"


def test_other_metrics_carry_no_display_unit() -> None:
    entry = _month()
    for code, unit in (
        ("water_consumed_kl", "KL"),
        ("operational_ghg_per_capita_kgco2e", "kgCO2e/person"),
        ("waste_per_capita_kg", "kg/person"),
    ):
        value = Value(code, "water", "calculation", Decimal("1"), unit)
        assert "display_unit" not in _display_item(value, entry, "July 2026", context=False), code
