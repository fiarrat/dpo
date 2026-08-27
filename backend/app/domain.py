"""
Доменная модель: типы обращений, категории заявителей, флаги и правила сроков.

Единственный источник правды по срокам — таблица SLA_RULES ниже. Каждое правило
несёт ссылку на норму ФЗ-152, чтобы в интерфейсе и в драфте ответа всегда было
видно, откуда взялась дата.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


# --------------------------------------------------------------------------- #
#  Перечисления
# --------------------------------------------------------------------------- #

class Channel(str, Enum):
    """Как обращение попало к оператору."""
    EMAIL = "EMAIL"
    PAPER = "PAPER"
    PORTAL = "PORTAL"
    MANUAL = "MANUAL"


class RequesterKind(str, Enum):
    """Кто обращается — определяет применимую норму."""
    SUBJECT = "SUBJECT"                    # сам субъект ПД
    SUBJECT_REPRESENTATIVE = "SUBJECT_REPRESENTATIVE"  # представитель субъекта (нужна доверенность)
    RKN = "RKN"                            # Роскомнадзор (уполномоченный орган, ст. 20 ч. 4)
    OTHER_AUTHORITY = "OTHER_AUTHORITY"    # прокуратура, МВД, суд, ФНС и т.п.
    COMPANY = "COMPANY"                    # юрлицо (контрагент, вендор)
    UNKNOWN = "UNKNOWN"


class SubjectType(str, Enum):
    """Вид субъекта ПД — задан пользователем как ключевой фильтр."""
    EMPLOYEE = "EMPLOYEE"                  # работник
    FORMER_EMPLOYEE = "FORMER_EMPLOYEE"    # бывший работник
    CANDIDATE = "CANDIDATE"                # кандидат
    USER = "USER"                          # пользователь сервиса
    CONSUMER = "CONSUMER"                  # потребитель
    COUNTERPARTY_REP = "COUNTERPARTY_REP"  # представитель контрагента
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


SUBJECT_TYPE_LABELS = {
    SubjectType.EMPLOYEE: "Работник",
    SubjectType.FORMER_EMPLOYEE: "Бывший работник",
    SubjectType.CANDIDATE: "Кандидат",
    SubjectType.USER: "Пользователь",
    SubjectType.CONSUMER: "Потребитель",
    SubjectType.COUNTERPARTY_REP: "Представитель контрагента",
    SubjectType.OTHER: "Иное",
    SubjectType.UNKNOWN: "Не определён",
}


class RequestType(str, Enum):
    """
    Тип обращения. Разделён на три блока: обращения субъектов ПД,
    обращения/запросы Роскомнадзора и «не про персональные данные».
    """
    # --- субъект ПД -------------------------------------------------------- #
    ACCESS = "ACCESS"                              # доступ к своим ПД / перечень сведений ч.7 ст.14
    CONFIRM_PROCESSING = "CONFIRM_PROCESSING"      # подтверждение факта обработки
    RECTIFICATION = "RECTIFICATION"                # уточнение неточных/неполных ПД
    BLOCKING = "BLOCKING"                          # требование блокирования
    ERASURE = "ERASURE"                            # уничтожение ПД
    CONSENT_WITHDRAWAL = "CONSENT_WITHDRAWAL"      # отзыв согласия
    STOP_MARKETING = "STOP_MARKETING"              # прекратить обработку в целях продвижения
    UNLAWFUL_PROCESSING = "UNLAWFUL_PROCESSING"    # заявление о неправомерной обработке
    AUTOMATED_DECISION = "AUTOMATED_DECISION"      # возражение против автоматизированного решения
    CROSS_BORDER_INFO = "CROSS_BORDER_INFO"        # сведения о трансграничной передаче
    SUBJECT_COMPLAINT = "SUBJECT_COMPLAINT"        # жалоба субъекта без конкретного требования

    # --- Роскомнадзор и иные органы --------------------------------------- #
    RKN_INFO_REQUEST = "RKN_INFO_REQUEST"          # запрос информации, ст. 20 ч. 4
    RKN_SUBJECT_COMPLAINT = "RKN_SUBJECT_COMPLAINT"  # жалоба субъекта, пересланная РКН
    RKN_ORDER = "RKN_ORDER"                        # предписание об устранении нарушения
    RKN_INSPECTION = "RKN_INSPECTION"              # уведомление о проверке / КНМ
    RKN_INCIDENT_FOLLOWUP = "RKN_INCIDENT_FOLLOWUP"  # запрос по инциденту (утечке)
    AUTHORITY_REQUEST = "AUTHORITY_REQUEST"        # запрос иного госоргана

    # --- не относится к персональным данным -------------------------------- #
    COOPERATION_OFFER = "COOPERATION_OFFER"        # предложение о сотрудничестве / КП
    CONSUMER_CLAIM = "CONSUMER_CLAIM"              # потребительская претензия (ЗоЗПП)
    TECH_SUPPORT = "TECH_SUPPORT"                  # техподдержка
    HR_QUESTION = "HR_QUESTION"                    # кадровый/зарплатный вопрос
    JOB_APPLICATION = "JOB_APPLICATION"            # отклик на вакансию / резюме
    BILLING = "BILLING"                            # счета, оплата, документы
    SPAM = "SPAM"                                  # спам / рассылка
    OTHER = "OTHER"                                # иное
    UNCLASSIFIED = "UNCLASSIFIED"                  # не удалось определить


REQUEST_TYPE_LABELS = {
    RequestType.ACCESS: "Доступ к своим ПД (ст. 14)",
    RequestType.CONFIRM_PROCESSING: "Подтверждение факта обработки",
    RequestType.RECTIFICATION: "Уточнение (исправление) ПД",
    RequestType.BLOCKING: "Блокирование ПД",
    RequestType.ERASURE: "Уничтожение ПД",
    RequestType.CONSENT_WITHDRAWAL: "Отзыв согласия",
    RequestType.STOP_MARKETING: "Прекратить обработку для продвижения",
    RequestType.UNLAWFUL_PROCESSING: "Заявление о неправомерной обработке",
    RequestType.AUTOMATED_DECISION: "Возражение против автоматизированного решения",
    RequestType.CROSS_BORDER_INFO: "Сведения о трансграничной передаче",
    RequestType.SUBJECT_COMPLAINT: "Жалоба субъекта ПД",
    RequestType.RKN_INFO_REQUEST: "РКН: запрос информации (ст. 20 ч. 4)",
    RequestType.RKN_SUBJECT_COMPLAINT: "РКН: жалоба субъекта",
    RequestType.RKN_ORDER: "РКН: предписание",
    RequestType.RKN_INSPECTION: "РКН: проверка / КНМ",
    RequestType.RKN_INCIDENT_FOLLOWUP: "РКН: запрос по инциденту",
    RequestType.AUTHORITY_REQUEST: "Запрос иного госоргана",
    RequestType.COOPERATION_OFFER: "Предложение о сотрудничестве",
    RequestType.CONSUMER_CLAIM: "Потребительская претензия",
    RequestType.TECH_SUPPORT: "Техническая поддержка",
    RequestType.HR_QUESTION: "Кадровый вопрос",
    RequestType.JOB_APPLICATION: "Отклик на вакансию",
    RequestType.BILLING: "Счета и документы",
    RequestType.SPAM: "Спам / рассылка",
    RequestType.OTHER: "Иное",
    RequestType.UNCLASSIFIED: "Не классифицировано",
}

#: Типы, которые заведомо не относятся к обработке ПД — красный флажок.
NON_PD_TYPES: frozenset[RequestType] = frozenset({
    RequestType.COOPERATION_OFFER,
    RequestType.CONSUMER_CLAIM,
    RequestType.TECH_SUPPORT,
    RequestType.HR_QUESTION,
    RequestType.JOB_APPLICATION,
    RequestType.BILLING,
    RequestType.SPAM,
})

#: Типы обращений от Роскомнадзора.
RKN_TYPES: frozenset[RequestType] = frozenset({
    RequestType.RKN_INFO_REQUEST,
    RequestType.RKN_SUBJECT_COMPLAINT,
    RequestType.RKN_ORDER,
    RequestType.RKN_INSPECTION,
    RequestType.RKN_INCIDENT_FOLLOWUP,
})

#: Типы обращений субъектов ПД.
SUBJECT_TYPES_OF_REQUEST: frozenset[RequestType] = frozenset({
    RequestType.ACCESS, RequestType.CONFIRM_PROCESSING, RequestType.RECTIFICATION,
    RequestType.BLOCKING, RequestType.ERASURE, RequestType.CONSENT_WITHDRAWAL,
    RequestType.STOP_MARKETING, RequestType.UNLAWFUL_PROCESSING,
    RequestType.AUTOMATED_DECISION, RequestType.CROSS_BORDER_INFO,
    RequestType.SUBJECT_COMPLAINT,
})


class Flag(str, Enum):
    RED = "RED"    # очевидно не связано с персональными данными
    BLUE = "BLUE"  # спорный момент — нужно решение DPO


class Status(str, Enum):
    NEW = "NEW"                          # поступило, не разобрано
    TRIAGE = "TRIAGE"                    # на квалификации
    IDENTITY_PENDING = "IDENTITY_PENDING"  # запрошено подтверждение личности/полномочий
    IN_PROGRESS = "IN_PROGRESS"          # сбор сведений по подразделениям
    DRAFTED = "DRAFTED"                  # драфт ответа готов
    ANSWERED = "ANSWERED"                # ответ направлен
    CLOSED = "CLOSED"                    # закрыто
    REJECTED = "REJECTED"                # мотивированный отказ (ч. 8 ст. 14)
    NOT_APPLICABLE = "NOT_APPLICABLE"    # не относится к ПД, передано профильной команде


STATUS_LABELS = {
    Status.NEW: "Новое",
    Status.TRIAGE: "Квалификация",
    Status.IDENTITY_PENDING: "Ждём подтверждение личности",
    Status.IN_PROGRESS: "В работе",
    Status.DRAFTED: "Драфт готов",
    Status.ANSWERED: "Ответ направлен",
    Status.CLOSED: "Закрыто",
    Status.REJECTED: "Мотивированный отказ",
    Status.NOT_APPLICABLE: "Не про ПД",
}

#: Статусы, в которых срок больше не тикает.
TERMINAL_STATUSES: frozenset[Status] = frozenset({
    Status.ANSWERED, Status.CLOSED, Status.REJECTED, Status.NOT_APPLICABLE,
})


class Urgency(str, Enum):
    OVERDUE = "OVERDUE"    # срок нарушен
    TODAY = "TODAY"        # истекает сегодня
    CRITICAL = "CRITICAL"  # <= 1 рабочего дня
    HIGH = "HIGH"          # <= 3 рабочих дней
    MEDIUM = "MEDIUM"      # <= 7 рабочих дней
    LOW = "LOW"            # > 7 рабочих дней
    NONE = "NONE"          # срок не применяется / обращение закрыто


URGENCY_LABELS = {
    Urgency.OVERDUE: "Просрочено",
    Urgency.TODAY: "Истекает сегодня",
    Urgency.CRITICAL: "Критично",
    Urgency.HIGH: "Высокая",
    Urgency.MEDIUM: "Средняя",
    Urgency.LOW: "Низкая",
    Urgency.NONE: "Без срока",
}

URGENCY_ORDER = {
    Urgency.OVERDUE: 0, Urgency.TODAY: 1, Urgency.CRITICAL: 2,
    Urgency.HIGH: 3, Urgency.MEDIUM: 4, Urgency.LOW: 5, Urgency.NONE: 6,
}


class Unit(str, Enum):
    WORKING_DAYS = "WORKING_DAYS"
    CALENDAR_DAYS = "CALENDAR_DAYS"
    HOURS = "HOURS"
    IMMEDIATE = "IMMEDIATE"


# --------------------------------------------------------------------------- #
#  Правила сроков
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Deadline:
    """Одна контрольная точка срока."""
    code: str
    title: str
    amount: int
    unit: Unit
    legal_ref: str
    note: str = ""
    #: Продление срока, если оно предусмотрено нормой (рабочие дни).
    extension_days: int = 0
    extension_ref: str = ""
    #: True — срок считается от подтверждения личности/полномочий, а не от даты письма.
    from_identity_confirmation: bool = False


@dataclass(frozen=True)
class SlaRule:
    """Набор контрольных точек для одного типа обращения."""
    request_type: RequestType
    deadlines: tuple[Deadline, ...]
    summary: str = ""


_D = Deadline

#: Базовый ответ по ст. 20 ч. 1-2 — 10 рабочих дней + продление на 5.
_ANSWER_10WD = _D(
    code="ANSWER",
    title="Ответ субъекту ПД",
    amount=10,
    unit=Unit.WORKING_DAYS,
    legal_ref="ст. 20 ч. 1, ч. 2 ФЗ-152; ст. 14 ч. 3 ФЗ-152",
    note="Сведения предоставляются в течение 10 рабочих дней с даты получения запроса.",
    extension_days=5,
    extension_ref="ст. 20 ч. 1 ФЗ-152 — продление не более чем на 5 рабочих дней "
                  "при направлении мотивированного уведомления с указанием причин",
    from_identity_confirmation=True,
)

SLA_RULES: dict[RequestType, SlaRule] = {
    RequestType.ACCESS: SlaRule(
        RequestType.ACCESS, (_ANSWER_10WD,),
        "10 рабочих дней на предоставление сведений по ч. 7 ст. 14, +5 рабочих дней при продлении.",
    ),
    RequestType.CONFIRM_PROCESSING: SlaRule(
        RequestType.CONFIRM_PROCESSING, (_ANSWER_10WD,),
        "10 рабочих дней на подтверждение факта обработки, +5 при продлении.",
    ),
    RequestType.CROSS_BORDER_INFO: SlaRule(
        RequestType.CROSS_BORDER_INFO, (_ANSWER_10WD,),
        "Сведения о трансграничной передаче входят в перечень ч. 7 ст. 14 — 10 рабочих дней.",
    ),
    RequestType.SUBJECT_COMPLAINT: SlaRule(
        RequestType.SUBJECT_COMPLAINT, (_ANSWER_10WD,),
        "Жалоба без конкретного требования: отвечаем в общий срок 10 рабочих дней.",
    ),

    RequestType.RECTIFICATION: SlaRule(
        RequestType.RECTIFICATION,
        (
            _D("BLOCK_ON_RECEIPT", "Блокирование ПД на период проверки", 0, Unit.IMMEDIATE,
               "ст. 21 ч. 1 ФЗ-152",
               "При выявлении неточных ПД блокирование осуществляется с момента обращения "
               "на период проверки, если это не нарушает права субъекта или третьих лиц."),
            _D("RECTIFY", "Уточнить ПД и снять блокирование", 7, Unit.WORKING_DAYS,
               "ст. 21 ч. 2 ФЗ-152",
               "7 рабочих дней со дня представления субъектом сведений, подтверждающих неточность.",
               from_identity_confirmation=True),
            _D("NOTIFY", "Уведомить субъекта о внесённых изменениях", 7, Unit.WORKING_DAYS,
               "ст. 20 ч. 3 ФЗ-152",
               "Уведомление о внесённых изменениях направляется в тот же 7-дневный срок.",
               from_identity_confirmation=True),
        ),
        "Блокирование с момента обращения; уточнение и уведомление — 7 рабочих дней.",
    ),

    RequestType.BLOCKING: SlaRule(
        RequestType.BLOCKING,
        (
            _D("BLOCK_ON_RECEIPT", "Блокирование ПД на период проверки", 0, Unit.IMMEDIATE,
               "ст. 21 ч. 1 ФЗ-152",
               "Блокирование с момента обращения на период проверки."),
            _D("DECIDE", "Проверка и решение по блокированию", 7, Unit.WORKING_DAYS,
               "ст. 20 ч. 3 ФЗ-152",
               "Внесение изменений, уничтожение или блокирование — не более 7 рабочих дней "
               "со дня представления подтверждающих сведений.",
               from_identity_confirmation=True),
        ),
        "Блокирование немедленно, решение по существу — 7 рабочих дней.",
    ),

    RequestType.ERASURE: SlaRule(
        RequestType.ERASURE,
        (
            _D("ERASE", "Прекратить обработку и уничтожить ПД", 30, Unit.CALENDAR_DAYS,
               "ст. 21 ч. 4 ФЗ-152",
               "30 дней с даты достижения цели обработки, если иное не предусмотрено договором "
               "или законом (сроки хранения по ТК РФ, ФЗ-402, ФЗ-115 могут быть длиннее)."),
            _D("ACT", "Оформить акт об уничтожении и выгрузку из журнала", 30, Unit.CALENDAR_DAYS,
               "Приказ Роскомнадзора от 28.10.2022 № 179",
               "Подтверждение уничтожения оформляется актом и выгрузкой из журнала регистрации "
               "событий в ИСПДн."),
            _D("NOTIFY", "Уведомить субъекта", 10, Unit.WORKING_DAYS,
               "ст. 20 ч. 1 ФЗ-152",
               "О результатах рассмотрения требования уведомляем в общий 10-дневный срок.",
               extension_days=5,
               extension_ref="ст. 20 ч. 1 ФЗ-152",
               from_identity_confirmation=True),
        ),
        "Уничтожение — 30 дней; уведомление субъекта — 10 рабочих дней.",
    ),

    RequestType.CONSENT_WITHDRAWAL: SlaRule(
        RequestType.CONSENT_WITHDRAWAL,
        (
            _D("STOP_PROCESSING", "Прекратить обработку / уничтожить ПД", 30, Unit.CALENDAR_DAYS,
               "ст. 21 ч. 5 ФЗ-152",
               "30 дней с даты поступления отзыва. Обработка на иных основаниях ч. 1 ст. 6 "
               "(договор, закон, судебный акт) при отзыве согласия НЕ прекращается — "
               "это надо явно указать в ответе."),
            _D("NOTIFY", "Уведомить субъекта о результатах", 10, Unit.WORKING_DAYS,
               "ст. 20 ч. 1 ФЗ-152",
               "Ответ о том, какая обработка прекращена, а какая продолжается и на каком основании.",
               extension_days=5,
               extension_ref="ст. 20 ч. 1 ФЗ-152",
               from_identity_confirmation=True),
        ),
        "Прекращение обработки — 30 дней с даты отзыва; ответ субъекту — 10 рабочих дней.",
    ),

    RequestType.STOP_MARKETING: SlaRule(
        RequestType.STOP_MARKETING,
        (
            _D("STOP_NOW", "Немедленно прекратить обработку для продвижения", 0, Unit.IMMEDIATE,
               "ст. 15 ч. 2 ФЗ-152",
               "Оператор обязан немедленно прекратить по требованию субъекта обработку его ПД "
               "в целях продвижения товаров, работ, услуг."),
            _D("NOTIFY", "Уведомить субъекта", 10, Unit.WORKING_DAYS,
               "ст. 20 ч. 1 ФЗ-152", "", extension_days=5,
               extension_ref="ст. 20 ч. 1 ФЗ-152", from_identity_confirmation=True),
        ),
        "Прекращение — немедленно, подтверждение субъекту — 10 рабочих дней.",
    ),

    RequestType.UNLAWFUL_PROCESSING: SlaRule(
        RequestType.UNLAWFUL_PROCESSING,
        (
            _D("BLOCK_ON_RECEIPT", "Блокирование неправомерно обрабатываемых ПД", 0, Unit.IMMEDIATE,
               "ст. 21 ч. 1 ФЗ-152",
               "Блокирование с момента обращения или получения запроса на период проверки."),
            _D("STOP_UNLAWFUL", "Прекратить неправомерную обработку", 3, Unit.WORKING_DAYS,
               "ст. 21 ч. 3 ФЗ-152",
               "3 рабочих дня с даты выявления неправомерной обработки."),
            _D("ERASE_IF_IMPOSSIBLE", "Уничтожить ПД, если правомерность недостижима", 10,
               Unit.WORKING_DAYS, "ст. 21 ч. 3 ФЗ-152",
               "10 рабочих дней с даты выявления неправомерной обработки."),
            _D("NOTIFY", "Уведомить субъекта (и РКН, если запрос шёл через него)", 10,
               Unit.WORKING_DAYS, "ст. 21 ч. 3 ФЗ-152",
               "Уведомление об устранении нарушений или об уничтожении ПД."),
        ),
        "Блокирование немедленно; прекращение — 3 рабочих дня; уничтожение — 10 рабочих дней.",
    ),

    RequestType.AUTOMATED_DECISION: SlaRule(
        RequestType.AUTOMATED_DECISION,
        (
            _D("REVIEW", "Рассмотреть возражение и уведомить о результатах", 30, Unit.CALENDAR_DAYS,
               "ст. 16 ч. 3 ФЗ-152",
               "30 дней со дня получения возражения против решения, принятого исключительно "
               "на основании автоматизированной обработки."),
        ),
        "30 дней на рассмотрение возражения (ст. 16 ч. 3).",
    ),

    # --- Роскомнадзор ------------------------------------------------------ #
    RequestType.RKN_INFO_REQUEST: SlaRule(
        RequestType.RKN_INFO_REQUEST,
        (
            _D("ANSWER_RKN", "Направить информацию в Роскомнадзор", 10, Unit.WORKING_DAYS,
               "ст. 20 ч. 4 ФЗ-152",
               "10 рабочих дней с даты получения запроса уполномоченного органа.",
               extension_days=5,
               extension_ref="ст. 20 ч. 4 ФЗ-152 — продление не более чем на 5 рабочих дней "
                             "при направлении в адрес уполномоченного органа мотивированного "
                             "уведомления с указанием причин продления"),
        ),
        "10 рабочих дней (ст. 20 ч. 4), +5 рабочих дней при мотивированном продлении.",
    ),

    RequestType.RKN_SUBJECT_COMPLAINT: SlaRule(
        RequestType.RKN_SUBJECT_COMPLAINT,
        (
            _D("ANSWER_RKN", "Ответ в Роскомнадзор по жалобе субъекта", 10, Unit.WORKING_DAYS,
               "ст. 20 ч. 4 ФЗ-152",
               "Срок, указанный в письме РКН, имеет приоритет — проверьте его вручную.",
               extension_days=5, extension_ref="ст. 20 ч. 4 ФЗ-152"),
            _D("NOTIFY_SUBJECT", "Уведомить субъекта об устранении нарушений", 10,
               Unit.WORKING_DAYS, "ст. 21 ч. 3 ФЗ-152",
               "Если нарушение подтвердилось — уведомляем и субъекта, и РКН."),
        ),
        "10 рабочих дней на ответ в РКН; при подтверждении нарушения — уведомление субъекта.",
    ),

    RequestType.RKN_ORDER: SlaRule(
        RequestType.RKN_ORDER,
        (
            _D("EXECUTE", "Исполнить предписание", 10, Unit.WORKING_DAYS,
               "ст. 23 ч. 3 п. 3 ФЗ-152; ФЗ-248",
               "ВНИМАНИЕ: срок исполнения указан в самом предписании и имеет приоритет "
               "над значением по умолчанию. Обязательно проставьте его вручную."),
            _D("REPORT", "Отчитаться об исполнении предписания", 10, Unit.WORKING_DAYS,
               "ФЗ-248 «О государственном контроле (надзоре)»",
               "Уведомление об исполнении с приложением подтверждающих документов."),
        ),
        "Срок берётся из текста предписания — значения по умолчанию условны.",
    ),

    RequestType.RKN_INSPECTION: SlaRule(
        RequestType.RKN_INSPECTION,
        (
            _D("PREPARE", "Подготовить документы к контрольному мероприятию", 10,
               Unit.WORKING_DAYS, "ФЗ-248 «О государственном контроле (надзоре)»",
               "Срок предоставления документов указывается в требовании/решении о КНМ."),
        ),
        "Срок из решения о проведении КНМ — проставляется вручную.",
    ),

    RequestType.RKN_INCIDENT_FOLLOWUP: SlaRule(
        RequestType.RKN_INCIDENT_FOLLOWUP,
        (
            _D("NOTIFY_24H", "Уведомление о факте инцидента", 24, Unit.HOURS,
               "ст. 21 ч. 3.1 п. 1 ФЗ-152",
               "24 часа с момента выявления инцидента (неправомерной передачи ПД)."),
            _D("NOTIFY_72H", "Результаты внутреннего расследования", 72, Unit.HOURS,
               "ст. 21 ч. 3.1 п. 2 ФЗ-152",
               "72 часа с момента выявления инцидента, включая сведения о виновных лицах."),
        ),
        "Инцидентные сроки: 24 часа на уведомление, 72 часа на результаты расследования.",
    ),

    RequestType.AUTHORITY_REQUEST: SlaRule(
        RequestType.AUTHORITY_REQUEST,
        (
            _D("ANSWER", "Ответ госоргану", 30, Unit.CALENDAR_DAYS,
               "ФЗ-59 ст. 12 (общий срок) / срок из самого запроса",
               "Срок и правовое основание зависят от органа: прокуратура, МВД, суд, ФНС. "
               "Проверьте срок, указанный в запросе, — он имеет приоритет."),
        ),
        "Срок указывается в самом запросе; по умолчанию — 30 дней.",
    ),

    # --- не про ПД --------------------------------------------------------- #
    RequestType.CONSUMER_CLAIM: SlaRule(
        RequestType.CONSUMER_CLAIM,
        (
            _D("ANSWER", "Ответ на претензию потребителя", 10, Unit.CALENDAR_DAYS,
               "ст. 22 Закона РФ «О защите прав потребителей»",
               "Вне периметра ФЗ-152. Передайте профильной команде — срок указан справочно."),
        ),
        "Не относится к ФЗ-152. Справочно: 10 дней по ЗоЗПП.",
    ),
}

#: Типы без нормативного срока — учитываются, но таймер не запускается.
NO_DEADLINE_TYPES: frozenset[RequestType] = frozenset({
    RequestType.COOPERATION_OFFER, RequestType.TECH_SUPPORT, RequestType.HR_QUESTION,
    RequestType.JOB_APPLICATION, RequestType.BILLING, RequestType.SPAM,
    RequestType.OTHER, RequestType.UNCLASSIFIED,
})


def rule_for(request_type: RequestType) -> SlaRule | None:
    return SLA_RULES.get(request_type)


def is_non_pd(request_type: RequestType) -> bool:
    return request_type in NON_PD_TYPES
