"""Производственный календарь и арифметика сроков."""
from datetime import date

import pytest

from app import calendar_ru as cal


def test_weekend_and_holiday_are_not_working_days():
    assert not cal.is_working_day(date(2026, 1, 3))    # суббота
    assert not cal.is_working_day(date(2026, 1, 7))    # Рождество
    assert not cal.is_working_day(date(2026, 5, 1))    # Праздник Весны и Труда
    assert cal.is_working_day(date(2026, 8, 27))       # обычный четверг


def test_transferred_holidays_from_government_decree():
    # 9 января 2026 — пятница, нерабочая по переносу с субботы 3 января.
    assert not cal.is_working_day(date(2026, 1, 9))
    # 11 мая 2026 — понедельник, перенос с субботы 9 мая.
    assert not cal.is_working_day(date(2026, 5, 11))


def test_working_saturday_is_a_working_day():
    # 1 ноября 2025 — рабочая суббота по постановлению № 1335.
    assert cal.is_working_day(date(2025, 11, 1))


def test_add_working_days_skips_new_year_holidays():
    # 10 рабочих дней от 24.12.2025 переваливают через каникулы 1-11 января.
    assert cal.add_working_days(date(2025, 12, 24), 10) == date(2026, 1, 19)


def test_add_working_days_counts_from_next_day():
    # День получения запроса в срок не входит (ст. 191 ГК РФ).
    assert cal.add_working_days(date(2026, 8, 27), 1) == date(2026, 8, 28)
    assert cal.add_working_days(date(2026, 8, 27), 0) == date(2026, 8, 27)


def test_calendar_deadline_moves_to_next_working_day():
    # 30 календарных дней от 05.12.2026 -> 04.01.2027 (нерабочий) -> 11.01.2027.
    assert cal.add_calendar_days(date(2026, 12, 5), 30) == date(2027, 1, 11)


def test_working_days_between_is_signed():
    assert cal.working_days_between(date(2026, 8, 27), date(2026, 8, 31)) == 2
    assert cal.working_days_between(date(2026, 8, 31), date(2026, 8, 27)) == -2
    assert cal.working_days_between(date(2026, 8, 27), date(2026, 8, 27)) == 0


def test_years_outside_calendar_are_flagged_approximate():
    assert cal.is_approximate(date(2030, 3, 1))
    assert not cal.is_approximate(date(2026, 3, 2))
