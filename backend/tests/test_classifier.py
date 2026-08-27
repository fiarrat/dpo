"""Классификация обращений и флажки."""
import pytest

from app.classifier import Signal, classify, extract_details, normalize
from app.domain import Flag, RequesterKind, RequestType, SubjectType


def codes(result, level):
    return {f.code for f in result.flags if f.level is level}


@pytest.mark.parametrize("subject, body, expected", [
    ("Запрос сведений",
     "Прошу предоставить сведения о моих персональных данных по части 7 статьи 14.",
     RequestType.ACCESS),
    ("Отзыв согласия",
     "Настоящим отзываю свое согласие на обработку персональных данных.",
     RequestType.CONSENT_WITHDRAWAL),
    ("Удаление",
     "Прошу уничтожить мои персональные данные и удалить мой аккаунт.",
     RequestType.ERASURE),
    ("Исправление",
     "В договоре указана неверная фамилия, прошу уточнить мои персональные данные.",
     RequestType.RECTIFICATION),
    ("Рассылка",
     "Прекратите рекламную рассылку, не желаю получать рекламу.",
     RequestType.STOP_MARKETING),
    ("Жалоба",
     "Мои данные обрабатываются без моего согласия, это неправомерная обработка.",
     RequestType.UNLAWFUL_PROCESSING),
])
def test_subject_request_types_are_recognised(subject, body, expected):
    result = classify(body=body, subject_line=subject)
    assert result.request_type is expected
    assert result.confidence > 0.4


def test_rkn_sender_is_detected_by_domain():
    r = classify(body="Просим представить информацию.", subject_line="Запрос",
                 from_email="office@77.rkn.gov.ru")
    assert r.requester_kind is RequesterKind.RKN
    assert r.request_type is RequestType.RKN_INFO_REQUEST


def test_order_from_rkn_is_classified_as_order():
    r = classify(
        body="Предписание об устранении выявленного нарушения. Устранить выявленные "
             "нарушения в срок до 15.09.2026.",
        subject_line="Предписание", from_email="office@rkn.gov.ru")
    assert r.request_type is RequestType.RKN_ORDER


def test_rkn_document_from_foreign_domain_is_flagged():
    r = classify(body="Предписание об устранении выявленного нарушения.",
                 subject_line="Предписание", from_email="scam@evil.example")
    assert "RKN_TYPE_WRONG_SENDER" in codes(r, Flag.BLUE)


@pytest.mark.parametrize("body, expected", [
    ("Я работаю в вашей компании по трудовому договору.", SubjectType.EMPLOYEE),
    ("Я направлял резюме и проходил собеседование на должность.", SubjectType.CANDIDATE),
    ("Я зарегистрирован в вашем мобильном приложении, логин test.", SubjectType.USER),
    ("Мой заказ № 12 не доставлен, я как потребитель требую возврата.",
     SubjectType.CONSUMER),
    ("Я представитель вашего контрагента, мои данные указаны в договоре.",
     SubjectType.COUNTERPARTY_REP),
    ("Я бывший работник, был уволен в прошлом году.", SubjectType.FORMER_EMPLOYEE),
])
def test_subject_types_are_recognised(body, expected):
    assert classify(body=body).subject_type is expected


def test_cooperation_offer_gets_red_flag():
    r = classify(
        subject_line="Коммерческое предложение",
        body="Наша компания специализируется на разработке. Предлагаем вам сотрудничество.")
    assert r.request_type is RequestType.COOPERATION_OFFER
    assert any(c.startswith("NON_PD") for c in codes(r, Flag.RED))


def test_consumer_claim_gets_red_flag():
    r = classify(subject_line="Претензия",
                 body="Требую вернуть деньги за товар ненадлежащего качества по ЗоЗПП.")
    assert r.request_type is RequestType.CONSUMER_CLAIM
    assert any(c.startswith("NON_PD") for c in codes(r, Flag.RED))


def test_non_pd_request_mentioning_pd_is_blue_not_red():
    """Смешанное обращение не должно молча уехать в «не наше»."""
    r = classify(
        subject_line="Претензия",
        body="Требую вернуть деньги за товар ненадлежащего качества по ЗоЗПП. "
             "Также прошу удалить мои персональные данные из вашей базы согласно ФЗ-152.")
    assert "MIXED_PD_AND_NON_PD" in codes(r, Flag.BLUE)
    assert not codes(r, Flag.RED)


def test_representative_without_power_of_attorney_is_flagged():
    r = classify(body="Я, адвокат Соколов, действую от имени Иванова И.И. "
                      "Прошу предоставить сведения о персональных данных доверителя.")
    assert r.requester_kind is RequesterKind.SUBJECT_REPRESENTATIVE
    assert "REPRESENTATIVE_NO_POA" in codes(r, Flag.BLUE)


def test_representative_with_power_of_attorney_still_needs_scope_check():
    r = classify(body="Я, адвокат Соколов, действую от имени Иванова И.И. на основании "
                      "доверенности от 01.03.2026. Прошу предоставить сведения о его "
                      "персональных данных.")
    assert "REPRESENTATIVE_CHECK_POA" in codes(r, Flag.BLUE)


def test_missing_identity_is_flagged():
    r = classify(body="Прошу предоставить сведения о моих персональных данных.")
    assert "IDENTITY_NOT_PROVEN" in codes(r, Flag.BLUE)


def test_identity_present_removes_the_flag():
    r = classify(body="Я, Иванов, паспорт 45 09 123456, прошу предоставить сведения "
                      "о моих персональных данных. Номер договора 12/2024.")
    assert "IDENTITY_NOT_PROVEN" not in codes(r, Flag.BLUE)


def test_consent_withdrawal_always_warns_about_other_legal_bases():
    r = classify(body="Отзываю свое согласие на обработку персональных данных.")
    assert "WITHDRAWAL_OTHER_BASIS" in codes(r, Flag.BLUE)


def test_erasure_warns_about_retention_periods():
    r = classify(body="Прошу уничтожить мои персональные данные.")
    assert "ERASURE_RETENTION_CONFLICT" in codes(r, Flag.BLUE)


def test_third_party_data_request_is_flagged():
    r = classify(body="Прошу предоставить персональные данные моего супруга, "
                      "которые вы обрабатываете.")
    assert "THIRD_PARTY_DATA" in codes(r, Flag.BLUE)


def test_composite_request_is_flagged():
    r = classify(body="Прошу предоставить сведения о моих персональных данных, "
                      "а также уничтожить мои персональные данные и отозвать согласие.")
    assert "COMPOSITE_REQUEST" in codes(r, Flag.BLUE)
    assert r.secondary_types


def test_authority_deadline_in_document_is_surfaced():
    r = classify(subject_line="Предписание",
                 body="Предписание об устранении. Устранить нарушения в срок до 30.09.2026.",
                 from_email="office@rkn.gov.ru")
    assert "AUTHORITY_DEADLINE_FOUND" in codes(r, Flag.BLUE)
    assert r.extracted["deadline_in_document"] == "30.09.2026"


def test_details_are_extracted_without_storing_passport_number():
    d = extract_details("Иванов, паспорт 45 09 998877, тел. +7 999 123-45-67, "
                        "ivan@mail.ru, ИНН 7712345678, договор № А-15/24")
    # Факт наличия документа фиксируем, сам номер — нет (ст. 5 ч. 5 ФЗ-152:
    # объём данных не должен быть избыточным по отношению к цели).
    assert d["passport_present"] is True
    assert "998877" not in str(d)
    assert "45 09" not in str(d)
    assert d["inn"] == "7712345678"
    assert d["contract_number"] == "а-15/24"
    assert "ivan@mail.ru" in d["emails"]


def test_yo_is_normalised():
    assert normalize("Василёк") == normalize("Василек")
