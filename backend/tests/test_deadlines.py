"""Сроки по ст. 14, 20 и 21 ФЗ-152."""
from datetime import date, datetime

from app.deadlines import compute
from app.domain import RequestType, Status, Urgency

NOW = datetime(2026, 8, 27, 10, 0)
RECEIVED = datetime(2026, 8, 27, 9, 0)


def test_access_request_is_ten_working_days():
    r = compute(RequestType.ACCESS, RECEIVED, now=NOW)
    assert r.due_date == date(2026, 9, 10)
    assert "ст. 20" in r.primary.legal_ref


def test_access_extension_adds_five_working_days():
    base = compute(RequestType.ACCESS, RECEIVED, now=NOW).due_date
    extended = compute(RequestType.ACCESS, RECEIVED, extension_applied=True, now=NOW).due_date
    assert base == date(2026, 9, 10)
    assert extended == date(2026, 9, 17)


def test_rectification_is_seven_working_days_with_immediate_blocking():
    r = compute(RequestType.RECTIFICATION, RECEIVED, now=NOW)
    assert r.due_date == date(2026, 9, 7)
    codes = {d.code for d in r.immediate_actions}
    assert codes == {"BLOCK_ON_RECEIPT"}
    # Немедленная обязанность не должна делать всю строку «истекающей сегодня».
    assert r.urgency != Urgency.TODAY.value


def test_consent_withdrawal_has_thirty_days_and_ten_day_reply():
    r = compute(RequestType.CONSENT_WITHDRAWAL, RECEIVED, now=NOW)
    by_code = {d.code: d.due_date for d in r.deadlines}
    assert by_code["STOP_PROCESSING"] == date(2026, 9, 28)
    assert by_code["NOTIFY"] == date(2026, 9, 10)
    assert r.due_date == date(2026, 9, 10)  # главный срок — ближайший


def test_unlawful_processing_three_and_ten_working_days():
    r = compute(RequestType.UNLAWFUL_PROCESSING, RECEIVED, now=NOW)
    by_code = {d.code: d.due_date for d in r.deadlines}
    assert by_code["STOP_UNLAWFUL"] == date(2026, 9, 1)
    assert by_code["ERASE_IF_IMPOSSIBLE"] == date(2026, 9, 10)


def test_rkn_request_is_ten_working_days_under_article_20_part_4():
    r = compute(RequestType.RKN_INFO_REQUEST, RECEIVED, now=NOW)
    assert r.due_date == date(2026, 9, 10)
    assert "ст. 20 ч. 4" in r.primary.legal_ref
    assert r.primary.extension_days == 5


def test_incident_deadlines_are_in_hours():
    r = compute(RequestType.RKN_INCIDENT_FOLLOWUP, RECEIVED, now=NOW)
    by_code = {d.code: d.due_at for d in r.deadlines}
    assert by_code["NOTIFY_24H"] == datetime(2026, 8, 28, 9, 0)
    assert by_code["NOTIFY_72H"] == datetime(2026, 8, 30, 9, 0)


def test_manual_deadline_from_document_overrides_calculation():
    r = compute(RequestType.RKN_ORDER, RECEIVED,
                manual_due_date=date(2026, 9, 1), now=NOW)
    assert r.due_date == date(2026, 9, 1)
    assert r.primary.manual is True


def test_order_without_manual_date_warns():
    r = compute(RequestType.RKN_ORDER, RECEIVED, now=NOW)
    assert any("вручную" in w for w in r.warnings)


def test_identity_confirmation_restarts_the_clock():
    late = compute(RequestType.ACCESS, RECEIVED,
                   identity_confirmed_at=datetime(2026, 9, 3, 12, 0), now=NOW)
    assert late.due_date == date(2026, 9, 17)
    assert "подтверждени" in late.primary.counted_from_label


def test_answered_request_has_no_urgency():
    r = compute(RequestType.ACCESS, datetime(2026, 7, 1), status=Status.ANSWERED, now=NOW)
    assert r.urgency == Urgency.NONE.value


def test_overdue_request_reports_negative_days_left():
    r = compute(RequestType.ACCESS, datetime(2026, 8, 1), now=NOW)
    assert r.urgency == Urgency.OVERDUE.value
    assert r.primary.working_days_left < 0


def test_non_pd_type_has_no_deadline():
    r = compute(RequestType.COOPERATION_OFFER, RECEIVED, now=NOW)
    assert r.due_date is None
    assert r.urgency == Urgency.NONE.value
