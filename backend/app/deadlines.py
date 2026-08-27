"""
Движок сроков: превращает тип обращения + даты в конкретные контрольные точки.

Ключевые решения, заложенные в расчёт:

1. Течение срока начинается со следующего дня после события (ст. 191 ГК РФ),
   поэтому день получения запроса в счёт не идёт.
2. Если окончание календарного срока приходится на нерабочий день, днём
   окончания считается ближайший следующий рабочий день (ст. 193 ГК РФ).
3. Для запросов субъекта срок отсчитывается от даты получения НАДЛЕЖАЩЕГО
   запроса. Если запрос не содержит сведений по ч. 4 ст. 14 (документ,
   удостоверяющий личность; сведения, подтверждающие участие в отношениях с
   оператором; подпись), оператор запрашивает их, и «часы» стартуют заново от
   даты получения подтверждения. Это спорная позиция, поэтому система
   показывает ОБЕ даты: от письма и от подтверждения личности.
4. Для запросов Роскомнадзора и предписаний срок из текста документа всегда
   имеет приоритет над расчётным (поле manual_due_date).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from . import calendar_ru as cal
from .domain import (
    Deadline, RequestType, SlaRule, Status, TERMINAL_STATUSES, Unit, Urgency,
    NO_DEADLINE_TYPES, rule_for,
)


@dataclass
class ComputedDeadline:
    code: str
    title: str
    due_at: datetime | None
    due_date: date | None
    legal_ref: str
    note: str
    unit: str
    amount: int
    #: Дата, от которой считали.
    counted_from: date | None
    counted_from_label: str
    #: Крайняя дата с учётом продления, если норма его допускает.
    extended_due_date: date | None
    extension_days: int
    extension_ref: str
    #: Осталось рабочих дней (отрицательное — просрочка).
    working_days_left: int | None
    urgency: str
    #: Год вне производственного календаря — дата приблизительная.
    approximate: bool
    #: Дата задана вручную и переопределяет расчёт.
    manual: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["due_at"] = self.due_at.isoformat() if self.due_at else None
        d["due_date"] = self.due_date.isoformat() if self.due_date else None
        d["counted_from"] = self.counted_from.isoformat() if self.counted_from else None
        d["extended_due_date"] = self.extended_due_date.isoformat() if self.extended_due_date else None
        return d


def _urgency(due: date | None, today: date, status: Status) -> Urgency:
    if status in TERMINAL_STATUSES or due is None:
        return Urgency.NONE
    if due < today:
        return Urgency.OVERDUE
    if due == today:
        return Urgency.TODAY
    left = cal.working_days_between(today, due)
    if left <= 1:
        return Urgency.CRITICAL
    if left <= 3:
        return Urgency.HIGH
    if left <= 7:
        return Urgency.MEDIUM
    return Urgency.LOW


def _urgency_hours(due_at: datetime | None, now: datetime, status: Status) -> Urgency:
    if status in TERMINAL_STATUSES or due_at is None:
        return Urgency.NONE
    delta = due_at - now
    if delta.total_seconds() < 0:
        return Urgency.OVERDUE
    if delta <= timedelta(hours=8):
        return Urgency.CRITICAL
    if delta <= timedelta(hours=24):
        return Urgency.TODAY
    return Urgency.HIGH


def compute_one(
    dl: Deadline,
    received_at: datetime,
    identity_confirmed_at: datetime | None,
    status: Status,
    now: datetime,
) -> ComputedDeadline:
    """Рассчитать одну контрольную точку."""
    today = now.date()

    # От какой даты считаем.
    if dl.from_identity_confirmation and identity_confirmed_at is not None:
        base_dt = identity_confirmed_at
        base_label = "от подтверждения личности / полномочий"
    else:
        base_dt = received_at
        base_label = "от даты получения обращения"
    base = base_dt.date()

    due_date: date | None = None
    due_at: datetime | None = None

    if dl.unit is Unit.IMMEDIATE:
        due_date = base
        due_at = base_dt
        base_label = "незамедлительно с момента обращения"
    elif dl.unit is Unit.HOURS:
        due_at = cal.add_hours(base_dt, dl.amount)
        due_date = due_at.date()
    elif dl.unit is Unit.WORKING_DAYS:
        due_date = cal.add_working_days(base, dl.amount)
    elif dl.unit is Unit.CALENDAR_DAYS:
        due_date = cal.add_calendar_days(base, dl.amount)

    extended: date | None = None
    if dl.extension_days and due_date is not None:
        extended = cal.add_working_days(due_date, dl.extension_days)

    if dl.unit is Unit.HOURS:
        urgency = _urgency_hours(due_at, now, status)
        left = None
    else:
        urgency = _urgency(due_date, today, status)
        left = cal.working_days_between(today, due_date) if due_date else None

    approx = cal.is_approximate(*(d for d in (base, due_date, extended) if d))

    return ComputedDeadline(
        code=dl.code,
        title=dl.title,
        due_at=due_at,
        due_date=due_date,
        legal_ref=dl.legal_ref,
        note=dl.note,
        unit=dl.unit.value,
        amount=dl.amount,
        counted_from=base,
        counted_from_label=base_label,
        extended_due_date=extended,
        extension_days=dl.extension_days,
        extension_ref=dl.extension_ref,
        working_days_left=left,
        urgency=urgency.value,
        approximate=approx,
    )


@dataclass
class DeadlineReport:
    """Итог по обращению: все контрольные точки + агрегированная срочность."""
    request_type: str
    summary: str
    deadlines: list[ComputedDeadline]
    primary: ComputedDeadline | None
    urgency: str
    due_date: date | None
    approximate: bool
    warnings: list[str]
    #: Обязанности «с момента обращения» — отдельный сигнал, не влияющий на
    #: срочность строки в реестре (иначе любое требование об уточнении вечно
    #: висело бы как «истекает сегодня»).
    immediate_actions: list[ComputedDeadline] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "request_type": self.request_type,
            "summary": self.summary,
            "deadlines": [d.to_dict() for d in self.deadlines],
            "primary": self.primary.to_dict() if self.primary else None,
            "urgency": self.urgency,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "approximate": self.approximate,
            "warnings": self.warnings,
            "immediate_actions": [d.to_dict() for d in self.immediate_actions],
        }


def compute(
    request_type: RequestType,
    received_at: datetime,
    *,
    status: Status = Status.NEW,
    identity_confirmed_at: datetime | None = None,
    manual_due_date: date | None = None,
    extension_applied: bool = False,
    now: datetime | None = None,
) -> DeadlineReport:
    """
    Полный расчёт по обращению.

    manual_due_date переопределяет главный срок — это обязательный сценарий для
    предписаний РКН и запросов госорганов, где срок написан в самом документе.
    """
    now = now or datetime.now()
    today = now.date()
    warnings: list[str] = []

    rule: SlaRule | None = rule_for(request_type)
    if rule is None:
        if request_type not in NO_DEADLINE_TYPES:
            warnings.append(f"Для типа {request_type.value} не задано правило срока.")
        return DeadlineReport(
            request_type=request_type.value,
            summary="Нормативный срок ответа не применяется.",
            deadlines=[], primary=None, urgency=Urgency.NONE.value,
            due_date=None, approximate=False, warnings=warnings,
            immediate_actions=[],
        )

    computed = [
        compute_one(dl, received_at, identity_confirmed_at, status, now)
        for dl in rule.deadlines
    ]

    # Главный срок — ближайшая по дате точка, которая ещё не «мгновенная».
    dated = [c for c in computed if c.due_date and c.unit != Unit.IMMEDIATE.value]
    primary = min(dated, key=lambda c: c.due_date) if dated else (computed[0] if computed else None)

    if primary is not None:
        if extension_applied and primary.extended_due_date:
            primary.due_date = primary.extended_due_date
            primary.manual = False
            primary.note = (primary.note + " Применено продление срока.").strip()
            primary.working_days_left = cal.working_days_between(today, primary.due_date)
            primary.urgency = _urgency(primary.due_date, today, status).value
        if manual_due_date is not None:
            primary.due_date = manual_due_date
            primary.due_at = None
            primary.manual = True
            primary.working_days_left = cal.working_days_between(today, manual_due_date)
            primary.urgency = _urgency(manual_due_date, today, status).value
            primary.note = (primary.note + " Срок задан вручную из текста документа.").strip()

    if extension_applied and (primary is None or not primary.extension_days):
        warnings.append("Продление срока отмечено, но норма для этого типа обращения его не предусматривает.")

    if request_type in (RequestType.RKN_ORDER, RequestType.RKN_INSPECTION,
                        RequestType.AUTHORITY_REQUEST) and manual_due_date is None:
        warnings.append(
            "Срок указан в самом документе и имеет приоритет — проставьте его вручную "
            "в поле «Срок из документа»."
        )

    from .domain import URGENCY_ORDER
    immediate = [c for c in computed if c.unit == Unit.IMMEDIATE.value]
    timed = [c for c in computed if c.unit != Unit.IMMEDIATE.value]
    if primary is not None and primary.manual:
        # Ручной срок из документа перекрывает расчётные точки.
        urgencies = [primary.urgency]
    else:
        urgencies = [c.urgency for c in timed] or [Urgency.NONE.value]
    worst = min(urgencies, key=lambda u: URGENCY_ORDER[Urgency(u)])

    approximate = any(c.approximate for c in computed)
    if approximate:
        warnings.append(
            "Часть дат попадает на год, отсутствующий в производственном календаре, — "
            "расчёт приблизительный (суббота/воскресенье + ст. 112 ТК РФ без переносов). "
            "Обновите app/data/production_calendar.json."
        )

    return DeadlineReport(
        request_type=request_type.value,
        summary=rule.summary,
        deadlines=computed,
        primary=primary,
        urgency=worst,
        due_date=primary.due_date if primary else None,
        approximate=approximate,
        warnings=warnings,
        immediate_actions=immediate,
    )
