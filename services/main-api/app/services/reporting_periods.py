from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.enums import SubmissionStatus
from app.models.sustainability import ReportingPeriod, Submission


def institutional_year_month(
    settings: Settings,
    clock: Callable[[], datetime] | None = None,
) -> tuple[int, int]:
    instant = (clock or (lambda: datetime.now(UTC)))()
    if instant.tzinfo is None:
        raise ValueError("institutional clock must return a timezone-aware datetime")
    local = instant.astimezone(ZoneInfo(settings.INSTITUTION_TIMEZONE))
    return local.year, local.month


def current_reporting_period(
    db: Session,
    settings: Settings,
    clock: Callable[[], datetime] | None = None,
) -> ReportingPeriod:
    year, month = institutional_year_month(settings, clock)
    period = db.scalar(
        select(ReportingPeriod).where(
            ReportingPeriod.year == year,
            ReportingPeriod.month == month,
        )
    )
    if period is None:
        raise HTTPException(status_code=503, detail="Current reporting period is not configured.")
    return period


def manager_entry_periods(
    db: Session,
    settings: Settings,
    clock: Callable[[], datetime] | None = None,
) -> list[ReportingPeriod]:
    """The reporting months a Manager may enter, newest (current) first.

    The window is the current institutional month plus the
    ``MANAGER_ENTRY_WINDOW_MONTHS - 1`` months before it. Only months that exist
    in ``reporting_periods`` are returned; the current month must exist.
    """
    year, month = institutional_year_month(settings, clock)
    wanted = []
    for offset in range(settings.MANAGER_ENTRY_WINDOW_MONTHS):
        index = year * 12 + (month - 1) - offset
        wanted.append((index // 12, index % 12 + 1))
    rows = db.scalars(
        select(ReportingPeriod).where(
            or_(*(and_(ReportingPeriod.year == y, ReportingPeriod.month == m) for y, m in wanted))
        )
    ).all()
    by_month = {(item.year, item.month): item for item in rows}
    if (year, month) not in by_month:
        raise HTTPException(status_code=503, detail="Current reporting period is not configured.")
    return [by_month[key] for key in wanted if key in by_month]


def _window_message(settings_months: int) -> str:
    if settings_months <= 1:
        return "Managers may create submissions only for the current reporting month."
    return (
        f"Managers may enter data only for the current reporting month and the previous "
        f"{settings_months - 1} month{'s' if settings_months > 2 else ''}."
    )


def require_period_in_entry_window(
    requested_period_id: object,
    entry_periods: Sequence[ReportingPeriod],
    window_months: int,
) -> ReportingPeriod:
    """Return the requested period if a Manager may create data for it."""
    period = next((item for item in entry_periods if item.id == requested_period_id), None)
    if period is None:
        raise HTTPException(status_code=409, detail=_window_message(window_months))
    if not period.is_open:
        raise HTTPException(
            status_code=409,
            detail="This reporting period is closed for normal manager entry.",
        )
    return period


def require_manager_mutation(
    submission: Submission,
    entry_periods: Sequence[ReportingPeriod],
) -> None:
    """A draft may change only while its month is inside the open entry window.

    A submission an Admin returned for correction stays editable whatever its
    month, exactly as before.
    """
    if submission.status == SubmissionStatus.CORRECTION_REQUESTED:
        return
    if submission.status != SubmissionStatus.DRAFT:
        raise HTTPException(
            status_code=409,
            detail="This monthly submission has already been submitted and is read-only.",
        )
    open_ids = {item.id for item in entry_periods if item.is_open}
    if submission.reporting_period_id not in open_ids:
        raise HTTPException(
            status_code=409,
            detail="This reporting period is closed for normal manager entry.",
        )
