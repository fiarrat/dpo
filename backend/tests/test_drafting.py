"""Плейсхолдеры, подбор шаблонов и чек-лист."""
from datetime import date, datetime

from app.deadlines import compute
from app.domain import RequesterKind, RequestType, Status, SubjectType
from app.drafting import (
    build_checklist, build_context, fallback_draft, fill, find_placeholders,
    rank_templates,
)


class FakeRequest:
    def __init__(self, **kw):
        defaults = dict(
            id=1, reg_number="ПД-2026-000001", request_type=RequestType.ACCESS.value,
            subject_type=SubjectType.EMPLOYEE.value,
            requester_kind=RequesterKind.SUBJECT.value,
            requester_name="Иванов И. И.", requester_email="i@mail.ru",
            received_at=datetime(2026, 8, 27, 9, 0), inbox_email="privacy@romashka.ru",
            legal_entity_id=1, legal_entity_mentioned="ООО «Ромашка»", service_id=None,
            service_mentioned="", status=Status.NEW.value, assignee="",
            summary="", identity_confirmed_at=None, extension_applied=False,
            body_text="текст обращения",
        )
        defaults.update(kw)
        self.__dict__.update(defaults)


class FakeTemplate:
    def __init__(self, id, title, request_types=(), subject_types=(), requester_kinds=(),
                 legal_entity_id=None, usage_count=0, is_active=True):
        self.id, self.title = id, title
        self.request_types = list(request_types)
        self.subject_types = list(subject_types)
        self.requester_kinds = list(requester_kinds)
        self.legal_entity_id = legal_entity_id
        self.usage_count, self.is_active = usage_count, is_active


class FakeEntity:
    name = "ООО «Ромашка»"
    address = "г. Москва"
    inn = "7701234567"
    rkn_operator_number = "77-24-000123"
    dpo_name = "Зимина Т. А."
    dpo_email = "privacy@romashka.ru"


def test_all_placeholder_styles_are_found():
    found = find_placeholders("{{ФИО}} {НОМЕР} [СРОК] <ЮЛ>")
    assert found == ["ФИО", "НОМЕР", "СРОК", "ЮЛ"]


def test_placeholders_are_filled_from_context():
    body, unresolved = fill(
        "Уважаемый {{ФИО}}, запрос {{номер}} от {{дата обращения}}, срок {{СРОК}}.",
        {"requester_name": "Иванов И. И.", "reg_number": "ПД-1",
         "received_date": "27.08.2026", "due_date": "10.09.2026"})
    assert body == "Уважаемый Иванов И. И., запрос ПД-1 от 27.08.2026, срок 10.09.2026."
    assert unresolved == []


def test_unknown_placeholder_stays_visible_and_is_reported():
    body, unresolved = fill("Оператор {{НЕИЗВЕСТНО}}.", {})
    assert "[НЕИЗВЕСТНО]" in body     # нельзя молча подставить пустоту
    assert unresolved == ["НЕИЗВЕСТНО"]


def test_context_carries_calculated_deadline():
    req = FakeRequest()
    report = compute(RequestType.ACCESS, req.received_at, now=datetime(2026, 8, 27, 10, 0))
    ctx = build_context(request=req, deadlines=report, legal_entity=FakeEntity(),
                        today=date(2026, 8, 27))
    assert ctx["due_date"] == "10.09.2026"
    assert ctx["rkn_operator_number"] == "77-24-000123"
    assert ctx["today"] == "27.08.2026"


def test_template_ranking_prefers_exact_type_and_subject_match():
    req = FakeRequest()
    templates = [
        FakeTemplate(1, "Универсальный"),
        FakeTemplate(2, "Доступ", request_types=[RequestType.ACCESS.value]),
        FakeTemplate(3, "Доступ для работников", request_types=[RequestType.ACCESS.value],
                     subject_types=[SubjectType.EMPLOYEE.value]),
        FakeTemplate(4, "Отзыв", request_types=[RequestType.CONSENT_WITHDRAWAL.value]),
    ]
    ranked = rank_templates(req, templates)
    assert ranked[0].template_id == 3
    assert 4 not in [m.template_id for m in ranked]   # чужой тип отброшен


def test_inactive_templates_are_skipped():
    req = FakeRequest()
    ranked = rank_templates(req, [FakeTemplate(1, "Выключен", is_active=False)])
    assert ranked == []


def test_checklist_demands_identity_check_when_not_confirmed():
    req = FakeRequest()
    report = compute(RequestType.ACCESS, req.received_at)
    items = build_checklist(req, report, [])
    assert any("ч. 4 ст. 14" in i["ref"] for i in items)


def test_checklist_for_erasure_covers_retention_and_act():
    req = FakeRequest(request_type=RequestType.ERASURE.value)
    report = compute(RequestType.ERASURE, req.received_at)
    text = " ".join(i["text"] for i in build_checklist(req, report, []))
    assert "хранения" in text
    assert "акт" in text.lower()


def test_checklist_includes_immediate_blocking_for_rectification():
    req = FakeRequest(request_type=RequestType.RECTIFICATION.value)
    report = compute(RequestType.RECTIFICATION, req.received_at)
    text = " ".join(i["text"] for i in build_checklist(req, report, []))
    assert "блокирован" in text.lower()


def test_extension_requires_motivated_notice():
    req = FakeRequest(extension_applied=True)
    report = compute(RequestType.ACCESS, req.received_at, extension_applied=True)
    items = build_checklist(req, report, [])
    assert any("мотивированное уведомление" in i["text"].lower() for i in items)


def test_fallback_draft_is_produced_without_templates():
    req = FakeRequest()
    report = compute(RequestType.ACCESS, req.received_at)
    ctx = build_context(request=req, deadlines=report, legal_entity=FakeEntity())
    body = fallback_draft(req, ctx)
    assert "части 7 статьи 14" in body or "ч. 7 ст. 14" in body or "14" in body
    assert "ПД-2026-000001" in body


def test_fallback_draft_for_rkn_is_addressed_to_the_authority():
    req = FakeRequest(request_type=RequestType.RKN_INFO_REQUEST.value,
                      requester_kind=RequesterKind.RKN.value)
    report = compute(RequestType.RKN_INFO_REQUEST, req.received_at)
    ctx = build_context(request=req, deadlines=report, legal_entity=FakeEntity())
    assert "Роскомнадзор" in fallback_draft(req, ctx)


def test_each_deadline_has_its_own_placeholder():
    """
    Универсальный {{СРОК}} нельзя ставить в предложение про уничтожение:
    у отзыва согласия ответ субъекту — 10 рабочих дней, а прекращение
    обработки — 30 календарных. Это разные даты.
    """
    req = FakeRequest(request_type=RequestType.CONSENT_WITHDRAWAL.value)
    report = compute(RequestType.CONSENT_WITHDRAWAL, req.received_at,
                     now=datetime(2026, 8, 27, 10, 0))
    ctx = build_context(request=req, deadlines=report, legal_entity=FakeEntity())

    body, unresolved = fill(
        "Ответ: {{СРОК ОТВЕТА}}. Уничтожение: {{СРОК ПРЕКРАЩЕНИЯ ОБРАБОТКИ}}.", ctx)
    assert unresolved == []
    assert "Ответ: 10.09.2026" in body
    assert "Уничтожение: 28.09.2026" in body


def test_extended_deadline_placeholder():
    req = FakeRequest()
    report = compute(RequestType.ACCESS, req.received_at, extension_applied=True,
                     now=datetime(2026, 8, 27, 10, 0))
    ctx = build_context(request=req, deadlines=report, legal_entity=FakeEntity())
    body, _ = fill("Продлено до {{СРОК С ПРОДЛЕНИЕМ}}.", ctx)
    assert "17.09.2026" in body
