"""
Производственный календарь РФ: расчёт рабочих дней.

Сроки по ФЗ-152 считаются в РАБОЧИХ днях (ст. 20, ст. 21 ч. 1-3) либо в
КАЛЕНДАРНЫХ днях (ст. 21 ч. 4-5 — «тридцать дней», ст. 16 ч. 3). Поэтому нужен
корректный производственный календарь с учётом переносов, утверждаемых
постановлениями Правительства РФ.

Календарь хранится в data/production_calendar.json и может редактироваться
без изменения кода — это обязательная ежегодная процедура сопровождения.
Для годов, отсутствующих в файле, применяется консервативный fallback:
суббота/воскресенье + нерабочие праздничные дни по ст. 112 ТК РФ БЕЗ переносов.
Такой fallback помечается флагом `approximate`, и интерфейс показывает
предупреждение — чтобы срок никогда не «поехал» молча.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable

DATA_DIR = Path(__file__).parent / "data"
CALENDAR_PATH = DATA_DIR / "production_calendar.json"

# Нерабочие праздничные дни, ст. 112 ТК РФ (без учёта переносов).
FIXED_HOLIDAYS: set[tuple[int, int]] = {
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),  # новогодние + Рождество
    (2, 23),   # День защитника Отечества
    (3, 8),    # Международный женский день
    (5, 1),    # Праздник Весны и Труда
    (5, 9),    # День Победы
    (6, 12),   # День России
    (11, 4),   # День народного единства
}


class CalendarYear:
    """Данные одного года производственного календаря."""

    __slots__ = ("year", "holidays", "workdays", "approximate", "source")

    def __init__(
        self,
        year: int,
        holidays: Iterable[date] = (),
        workdays: Iterable[date] = (),
        approximate: bool = False,
        source: str = "",
    ) -> None:
        self.year = year
        # holidays — нерабочие дни, выпадающие на пн-пт (праздники и перенесённые выходные)
        self.holidays = set(holidays)
        # workdays — рабочие субботы/воскресенья (перенесённые рабочие дни)
        self.workdays = set(workdays)
        self.approximate = approximate
        self.source = source


def _parse_days(items: Iterable[str], year: int) -> list[date]:
    out: list[date] = []
    for raw in items:
        d = datetime.strptime(raw, "%Y-%m-%d").date()
        if d.year != year:
            raise ValueError(f"Дата {raw} не относится к году {year}")
        out.append(d)
    return out


@lru_cache(maxsize=1)
def _load_calendar() -> dict[int, CalendarYear]:
    if not CALENDAR_PATH.exists():
        return {}
    raw = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    result: dict[int, CalendarYear] = {}
    for year_str, payload in raw.get("years", {}).items():
        year = int(year_str)
        result[year] = CalendarYear(
            year=year,
            holidays=_parse_days(payload.get("holidays", []), year),
            workdays=_parse_days(payload.get("workdays", []), year),
            approximate=bool(payload.get("approximate", False)),
            source=payload.get("source", ""),
        )
    return result


def reload_calendar() -> None:
    """Сбросить кэш после правки JSON-файла календаря."""
    _load_calendar.cache_clear()


def _fallback_year(year: int) -> CalendarYear:
    holidays = []
    for month, day in FIXED_HOLIDAYS:
        d = date(year, month, day)
        if d.weekday() < 5:
            holidays.append(d)
    return CalendarYear(year, holidays=holidays, approximate=True, source="fallback: ст. 112 ТК РФ без переносов")


def year_data(year: int) -> CalendarYear:
    return _load_calendar().get(year) or _fallback_year(year)


def is_working_day(d: date) -> bool:
    y = year_data(d.year)
    if d in y.workdays:
        return True
    if d in y.holidays:
        return False
    return d.weekday() < 5


def is_approximate(*days: date) -> bool:
    """True, если хотя бы один из годов посчитан по fallback-календарю."""
    return any(year_data(d.year).approximate for d in days)


def next_working_day(d: date) -> date:
    cur = d
    for _ in range(400):
        cur += timedelta(days=1)
        if is_working_day(cur):
            return cur
    raise RuntimeError("Не удалось найти рабочий день — проверьте производственный календарь")


def add_working_days(start: date, days: int) -> date:
    """
    Прибавить N рабочих дней. Течение срока начинается со следующего дня после
    события (ст. 191 ГК РФ), поэтому день получения запроса не считается.
    add_working_days(d, 0) возвращает сам день d.
    """
    if days <= 0:
        return start
    cur = start
    left = days
    for _ in range(days * 5 + 400):
        cur += timedelta(days=1)
        if is_working_day(cur):
            left -= 1
            if left == 0:
                return cur
    raise RuntimeError("Переполнение при расчёте рабочих дней")


def add_calendar_days(start: date, days: int) -> date:
    """
    Прибавить N календарных дней. Если окончание срока приходится на нерабочий
    день, днём окончания считается ближайший следующий рабочий день
    (ст. 193 ГК РФ).
    """
    end = start + timedelta(days=days)
    if not is_working_day(end):
        end = next_working_day(end)
    return end


def add_hours(start: datetime, hours: int) -> datetime:
    """Астрономические часы — для инцидентных сроков 24/72 ч (ст. 21 ч. 3.1)."""
    return start + timedelta(hours=hours)


def working_days_between(start: date, end: date) -> int:
    """
    Количество рабочих дней от start (не включая) до end (включая).
    Отрицательное значение, если end раньше start.
    """
    if end == start:
        return 0
    sign = 1 if end > start else -1
    lo, hi = (start, end) if sign > 0 else (end, start)
    count = 0
    cur = lo
    while cur < hi:
        cur += timedelta(days=1)
        if is_working_day(cur):
            count += 1
    return count * sign


def calendar_status() -> dict:
    """Сводка по загруженному календарю — выводится в интерфейсе."""
    cal = _load_calendar()
    return {
        "path": str(CALENDAR_PATH),
        "years": sorted(cal.keys()),
        "details": [
            {
                "year": y.year,
                "approximate": y.approximate,
                "source": y.source,
                "holidays": len(y.holidays),
                "working_weekends": len(y.workdays),
            }
            for y in sorted(cal.values(), key=lambda x: x.year)
        ],
    }
