from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.models.enums import OperationalDomain, RoleCode
from app.models.sustainability import ReportingPeriod
from app.services.reporting_periods import institutional_year_month
from tests.test_generic_submission_integration import _account, _login, _values
from tests.test_outreach_integration import _programme


def _months(engine: Engine, wanted: list[tuple[int, int]]) -> list[ReportingPeriod]:
    """Create the given (year offset, month) periods in a base year with none of them."""
    with Session(engine) as db:
        year = 2000
        while db.scalar(
            select(ReportingPeriod.id).where(
                or_(*(and_(ReportingPeriod.year == year + dy, ReportingPeriod.month == m) for dy, m in wanted))
            )
        ):
            year += 1
            if year > 2200:
                raise RuntimeError("No free test months are available.")
        periods = [
            ReportingPeriod(
                year=year + dy,
                month=m,
                period_start=date(year + dy, m, 1),
                period_end=date(year + dy, m, 28),
                is_open=True,
            )
            for dy, m in wanted
        ]
        db.add_all(periods)
        db.commit()
        for period in periods:
            db.refresh(period)
            db.expunge(period)
        return periods


def _window_periods(
    engine: Engine,
) -> tuple[ReportingPeriod, ReportingPeriod, ReportingPeriod, ReportingPeriod, ReportingPeriod]:
    """March..July of one year: too old, earliest allowed, previous, current (June), future."""
    too_old, earliest, previous, current, future = _months(engine, [(0, m) for m in (3, 4, 5, 6, 7)])
    return too_old, earliest, previous, current, future


def _client(engine: Engine, current: ReportingPeriod, window_months: int = 3) -> TestClient:
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=str(engine.url),
        EVIDENCE_ROOT=".local/test-evidence",
        MANAGER_ENTRY_WINDOW_MONTHS=window_months,
    )
    def clock() -> datetime:
        return datetime(current.year, current.month, 15, tzinfo=UTC)

    return TestClient(create_app(settings, engine, clock))


def test_asia_kolkata_month_boundary_uses_injected_clock() -> None:
    settings = Settings(APP_ENV="test", INSTITUTION_TIMEZONE="Asia/Kolkata")
    def before() -> datetime:
        return datetime(2026, 9, 30, 18, 29, tzinfo=UTC)

    def after() -> datetime:
        return datetime(2026, 9, 30, 18, 30, tzinfo=UTC)

    assert institutional_year_month(settings, before) == (2026, 9)
    assert institutional_year_month(settings, after) == (2026, 10)


def test_current_month_submit_once_and_historical_correction(
    postgres_engine: Engine,
) -> None:
    too_old, _earliest, _previous, current, future = _window_periods(postgres_engine)
    manager = _account(postgres_engine, RoleCode.MANAGER, OperationalDomain.TRANSPORT)
    admin = _account(postgres_engine, RoleCode.ADMIN)
    values = _values(postgres_engine, OperationalDomain.TRANSPORT, include_optional_zero=True, period=current)

    with _client(postgres_engine, current) as manager_client:
        csrf = _login(manager_client, manager)
        period_response = manager_client.get("/api/manager/transport/current-period")
        assert period_response.status_code == 200
        assert period_response.json() == {
            "id": str(current.id),
            "year": current.year,
            "month": current.month,
            "label": f"June {current.year}",
            "is_open": True,
            "is_current": True,
        }

        for forbidden in (too_old, future):
            response = manager_client.post(
                "/api/manager/transport/submissions",
                json={"reporting_period_id": str(forbidden.id), "remarks": None, "values": values},
                headers={"X-CSRF-Token": csrf},
            )
            assert response.status_code == 409

        payload = {
            "reporting_period_id": str(current.id),
            "remarks": "Current month draft",
            "values": values,
        }
        created = manager_client.post(
            "/api/manager/transport/submissions",
            json=payload,
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 201, created.text
        submission_id = created.json()["id"]
        repeated = manager_client.post(
            "/api/manager/transport/submissions",
            json={
                **payload,
                "remarks": "Saved again",
                "expected_row_version": created.json()["row_version"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert repeated.status_code == 201
        assert repeated.json()["id"] == submission_id
        assert manager_client.post(
            f"/api/manager/transport/submissions/{submission_id}/submit",
            headers={"X-CSRF-Token": csrf},
        ).status_code == 200
        assert manager_client.post(
            f"/api/manager/transport/submissions/{submission_id}/submit",
            headers={"X-CSRF-Token": csrf},
        ).status_code == 409

        with _client(postgres_engine, future) as future_client:
            future_csrf = _login(future_client, manager)
            assert future_client.put(
                f"/api/manager/transport/submissions/{submission_id}",
                json={
                    "remarks": "Forbidden old edit",
                    "values": values,
                    "expected_row_version": future_client.get(
                        f"/api/manager/transport/submissions/{submission_id}"
                    ).json()["row_version"],
                },
                headers={"X-CSRF-Token": future_csrf},
            ).status_code == 409

            with _client(postgres_engine, future) as admin_client:
                admin_csrf = _login(admin_client, admin)
                assert admin_client.post(
                    f"/api/admin/submissions/{submission_id}/request-correction",
                    json={"reason": "Historical correction required."},
                    headers={"X-CSRF-Token": admin_csrf},
                ).status_code == 200

            corrected = future_client.put(
                f"/api/manager/transport/submissions/{submission_id}",
                json={
                    "remarks": "Historical correction",
                    "values": values,
                    "expected_row_version": future_client.get(
                        f"/api/manager/transport/submissions/{submission_id}"
                    ).json()["row_version"],
                },
                headers={"X-CSRF-Token": future_csrf},
            )
            assert corrected.status_code == 200
            assert future_client.post(
                f"/api/manager/transport/submissions/{submission_id}/submit",
                headers={"X-CSRF-Token": future_csrf},
            ).status_code == 200
            detail = future_client.get(
                f"/api/manager/transport/submissions/{submission_id}"
            ).json()
            assert detail["revision_number"] == 2
            assert detail["status"] == "submitted"


@pytest.mark.parametrize("domain", list(OperationalDomain))
def test_entry_window_create_is_enforced_for_all_manager_domains(
    postgres_engine: Engine,
    domain: OperationalDomain,
) -> None:
    """The current month and the previous two are open; older and future months are not."""
    too_old, earliest, _previous, current, future = _window_periods(postgres_engine)
    manager = _account(postgres_engine, RoleCode.MANAGER, domain)
    with _client(postgres_engine, current) as client:
        csrf = _login(client, manager)
        if domain == OperationalDomain.OUTREACH:
            assert client.get("/api/manager/outreach/current-period").json()["id"] == str(current.id)
            denied = [
                client.post(
                    "/api/manager/outreach/programmes",
                    json=_programme(period),
                    headers={"X-CSRF-Token": csrf},
                )
                for period in (too_old, future)
            ]
            allowed = [
                client.post(
                    "/api/manager/outreach/programmes",
                    json=_programme(period),
                    headers={"X-CSRF-Token": csrf},
                )
                for period in (current, earliest)
            ]
        else:
            assert client.get(f"/api/manager/{domain.value}/current-period").json()["id"] == str(current.id)
            denied = [
                client.post(
                    f"/api/manager/{domain.value}/submissions",
                    json={
                        "reporting_period_id": str(period.id),
                        "remarks": None,
                        "values": _values(postgres_engine, domain, period=period),
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                for period in (too_old, future)
            ]
            allowed = [
                client.post(
                    f"/api/manager/{domain.value}/submissions",
                    json={
                        "reporting_period_id": str(period.id),
                        "remarks": None,
                        "values": _values(postgres_engine, domain, period=period),
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                for period in (current, earliest)
            ]
        assert [response.status_code for response in denied] == [409, 409]
        assert [response.status_code for response in allowed] == [201, 201], [item.text for item in allowed]


def test_periods_endpoint_lists_the_entry_window_newest_first(postgres_engine: Engine) -> None:
    too_old, earliest, previous, current, _future = _window_periods(postgres_engine)
    manager = _account(postgres_engine, RoleCode.MANAGER, OperationalDomain.ENERGY)
    with _client(postgres_engine, current) as client:
        _login(client, manager)
        periods = client.get("/api/manager/energy/periods").json()
    assert [item["id"] for item in periods] == [str(current.id), str(previous.id), str(earliest.id)]
    assert [item["is_current"] for item in periods] == [True, False, False]
    assert str(too_old.id) not in {item["id"] for item in periods}


def test_entry_window_crosses_the_year_boundary(postgres_engine: Engine) -> None:
    """January's window is January plus November and December of the year before."""
    october, november, december, january = _months(postgres_engine, [(0, 10), (0, 11), (0, 12), (1, 1)])
    manager = _account(postgres_engine, RoleCode.MANAGER, OperationalDomain.WATER)
    with _client(postgres_engine, january) as client:
        csrf = _login(client, manager)
        periods = client.get("/api/manager/water/periods").json()
        assert [item["id"] for item in periods] == [str(january.id), str(december.id), str(november.id)]
        responses = {
            period.month: client.post(
                "/api/manager/water/submissions",
                json={
                    "reporting_period_id": str(period.id),
                    "remarks": None,
                    "values": _values(postgres_engine, OperationalDomain.WATER, period=period),
                },
                headers={"X-CSRF-Token": csrf},
            ).status_code
            for period in (october, november)
        }
    assert responses == {10: 409, 11: 201}


def test_window_of_one_restores_current_month_only(postgres_engine: Engine) -> None:
    _too_old, _earliest, previous, current, _future = _window_periods(postgres_engine)
    manager = _account(postgres_engine, RoleCode.MANAGER, OperationalDomain.LPG)
    with _client(postgres_engine, current, window_months=1) as client:
        csrf = _login(client, manager)
        assert [item["id"] for item in client.get("/api/manager/lpg/periods").json()] == [str(current.id)]
        response = client.post(
            "/api/manager/lpg/submissions",
            json={
                "reporting_period_id": str(previous.id),
                "remarks": None,
                "values": _values(postgres_engine, OperationalDomain.LPG, period=previous),
            },
            headers={"X-CSRF-Token": csrf},
        )
    assert response.status_code == 409
    assert "current reporting month" in response.json()["error"]["message"]


def test_closed_month_inside_the_window_is_refused(postgres_engine: Engine) -> None:
    _too_old, _earliest, previous, current, _future = _window_periods(postgres_engine)
    with Session(postgres_engine) as db:
        db.get(ReportingPeriod, previous.id).is_open = False  # type: ignore[union-attr]
        db.commit()
    manager = _account(postgres_engine, RoleCode.MANAGER, OperationalDomain.ENERGY)
    with _client(postgres_engine, current) as client:
        csrf = _login(client, manager)
        response = client.post(
            "/api/manager/energy/submissions",
            json={
                "reporting_period_id": str(previous.id),
                "remarks": None,
                "values": _values(postgres_engine, OperationalDomain.ENERGY, period=previous),
            },
            headers={"X-CSRF-Token": csrf},
        )
    assert response.status_code == 409
    assert "closed" in response.json()["error"]["message"]


def test_previous_month_draft_is_editable_until_it_leaves_the_window(postgres_engine: Engine) -> None:
    """A May draft saved in June can be edited in July, but not in August."""
    months = _months(postgres_engine, [(0, m) for m in (5, 6, 7, 8)])
    may, june, july, august = months
    manager = _account(postgres_engine, RoleCode.MANAGER, OperationalDomain.ENERGY)
    values = _values(postgres_engine, OperationalDomain.ENERGY, period=may)
    with _client(postgres_engine, june) as client:
        csrf = _login(client, manager)
        created = client.post(
            "/api/manager/energy/submissions",
            json={"reporting_period_id": str(may.id), "remarks": "May, entered in June", "values": values},
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 201, created.text
        submission_id = created.json()["id"]
        current = client.get(f"/api/manager/energy/submissions/current?reporting_period_id={may.id}").json()
        assert current["id"] == submission_id

    def edit(at: ReportingPeriod) -> int:
        with _client(postgres_engine, at) as client:
            csrf = _login(client, manager)
            row_version = client.get(f"/api/manager/energy/submissions/{submission_id}").json()["row_version"]
            return client.put(
                f"/api/manager/energy/submissions/{submission_id}",
                json={"remarks": f"Edited in {at.month}", "values": values, "expected_row_version": row_version},
                headers={"X-CSRF-Token": csrf},
            ).status_code

    assert edit(july) == 200
    assert edit(august) == 409
