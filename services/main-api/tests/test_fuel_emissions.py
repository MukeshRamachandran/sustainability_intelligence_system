"""Fuel emissions: the public display aggregate petrol + fleet diesel (no database).

It adds two already-governed emission results of the same period. DG diesel and
LPG are never part of it, a missing component is never zero, and Scope 1 is not
touched by it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.historical.resolver import (
    FUEL_EMISSION_CONTRIBUTORS,
    FUEL_EMISSIONS,
    Entry,
    Value,
    _aggregate_display,
    _derive_fuel_emissions,
    _month_display,
)
from app.services import sustainability_formulas as formulas

PETROL, FLEET = FUEL_EMISSION_CONTRIBUTORS


def _entry(values: dict[str, str | None], **overrides: dict[str, object]) -> Entry:
    entry = Entry("2026-07", "Jul 2026", 2026, 7, "MONTHLY", date(2026, 7, 1), date(2026, 7, 31), "historical_verified")
    for code, amount in values.items():
        entry.values[code] = Value(
            code,
            "lpg" if code == "lpg_emissions" else "transport",
            "calculation",
            None if amount is None else Decimal(amount),
            "tCO2e",
            **overrides.get(code, {}),  # type: ignore[arg-type]
        )
    return entry


def _fuel(entry: Entry) -> Value | None:
    _derive_fuel_emissions(entry)
    return entry.values.get(FUEL_EMISSIONS)


def test_formula_is_petrol_plus_fleet_diesel_only() -> None:
    assert formulas.fuel_emissions_available_tco2e(Decimal("21.38"), Decimal("316.23")) == Decimal("337.61")
    assert formulas.fuel_emissions_available_tco2e(Decimal("0"), Decimal("316.23")) == Decimal("316.23")
    assert formulas.fuel_emissions_available_tco2e(Decimal("21.38"), Decimal("0")) == Decimal("21.38")
    assert formulas.fuel_emissions_available_tco2e(Decimal("0"), Decimal("0")) == Decimal("0")
    assert formulas.fuel_emissions_available_tco2e(None, None) is None
    assert FUEL_EMISSION_CONTRIBUTORS == ("transport_petrol_emissions", "transport_diesel_emissions")


def test_both_components_present_give_a_complete_sum() -> None:
    fuel = _fuel(_entry({PETROL: "21.38", FLEET: "316.23"}))
    assert fuel is not None
    assert fuel.value == Decimal("337.61")
    assert (fuel.unit, fuel.domain, fuel.kind) == ("tCO2e", "transport", "calculation")
    assert fuel.calculation_status == "COMPLETE"
    assert [item["label"] for item in fuel.contributors] == ["Petrol", "Fleet Diesel"]
    assert fuel.missing_contributors == []
    assert fuel.provenance["components"] == [PETROL, FLEET]


def test_an_explicit_zero_is_a_value_not_a_gap() -> None:
    for petrol, fleet, expected in (("0", "316.23", "316.23"), ("21.38", "0", "21.38"), ("0", "0", "0")):
        fuel = _fuel(_entry({PETROL: petrol, FLEET: fleet}))
        assert fuel is not None
        assert fuel.value == Decimal(expected)
        assert fuel.calculation_status == "COMPLETE"
        assert fuel.as_json()["status"] == "available"


def test_a_missing_component_is_excluded_and_reported_never_zero() -> None:
    cases: tuple[tuple[dict[str, str | None], str], ...] = (
        ({PETROL: "21.38"}, "Fleet Diesel"),
        ({PETROL: "21.38", FLEET: None}, "Fleet Diesel"),
        ({FLEET: "316.23"}, "Petrol"),
    )
    for values, missing in cases:
        fuel = _fuel(_entry(values))
        assert fuel is not None
        assert fuel.value == Decimal(next(v for v in values.values() if v is not None))
        assert fuel.calculation_status == "PARTIAL"
        assert [item["label"] for item in fuel.missing_contributors] == [missing]
        assert len(fuel.contributors) == 1


def test_nothing_is_published_when_neither_component_exists() -> None:
    assert _fuel(_entry({})) is None
    assert _fuel(_entry({PETROL: None, FLEET: None})) is None
    # DG diesel and LPG alone never produce a fuel figure.
    assert _fuel(_entry({"dg_diesel_emissions": "51.49", "lpg_emissions": "9.9"})) is None


def test_dg_diesel_and_lpg_are_never_included_and_scope1_is_untouched() -> None:
    entry = _entry(
        {
            PETROL: "21.38",
            FLEET: "316.23",
            "dg_diesel_emissions": "51.49",
            "lpg_emissions": "304.229562",
            "scope1_tco2e": "693.329562",
        }
    )
    before = {code: item.value for code, item in entry.values.items()}
    fuel = _fuel(entry)
    assert fuel is not None and fuel.value == Decimal("337.61")
    assert {code: item.value for code, item in entry.values.items() if code != FUEL_EMISSIONS} == before
    assert {item["code"] for item in fuel.contributors} == {PETROL, FLEET}


def test_a_component_covering_only_some_months_makes_the_sum_partial() -> None:
    window = ["2026-01", "2026-02", "2026-03"]
    entry = _entry(
        {PETROL: "6", FLEET: "40"},
        **{
            PETROL: {"coverage_status": "complete", "months_covered": window},
            FLEET: {"coverage_status": "partial", "months_covered": window[:2]},
        },
    )
    fuel = _fuel(entry)
    assert fuel is not None
    assert fuel.value == Decimal("46")
    assert fuel.coverage_status == "partial"
    assert fuel.calculation_status == "PARTIAL"
    assert fuel.months_covered == window
    statuses = {item["label"]: item["status"] for item in fuel.contributors}
    assert statuses == {"Petrol": "COMPLETE", "Fleet Diesel": "PARTIAL"}


def test_the_card_gets_a_display_item_and_a_partial_sum_is_never_month_context() -> None:
    month = _entry({PETROL: "2.12", FLEET: "23.41"})
    _derive_fuel_emissions(month)
    shown = _month_display(month, [], {})[FUEL_EMISSIONS]
    assert (shown["value"], shown["unit"], shown["display_context"]) == (25.53, "tCO2e", False)
    assert shown["calculation_status"] == "COMPLETE"

    year = Entry("2026-YTD", "2026 YTD", 2026, None, "YTD", date(2026, 1, 1), date(2026, 7, 31), "historical_aggregate")
    year.values[PETROL] = Value(PETROL, "transport", "calculation", Decimal("10"), "tCO2e", granularity="YTD")
    _derive_fuel_emissions(year)  # fleet diesel missing for the year: PARTIAL
    assert _aggregate_display(year, {})[FUEL_EMISSIONS]["calculation_status"] == "PARTIAL"
    empty_month = _entry({"lpg_emissions": "4.33"})
    assert FUEL_EMISSIONS not in _month_display(empty_month, [year], {})
