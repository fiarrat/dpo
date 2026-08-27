"""
Типовые ответы и генерация драфтов.

Пользователь загружает свои типовые ответы (docx / pdf / txt). Система находит
в них плейсхолдеры, подбирает подходящий шаблон под конкретное обращение,
подставляет данные из карточки и рассчитанные сроки и собирает чек-лист
проверок перед отправкой.

Плейсхолдеры, которые останутся незаполненными, не «затираются» пустотой —
они остаются видимыми в тексте и выводятся отдельным списком, чтобы ответ не
ушёл с потерянным реквизитом.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .deadlines import DeadlineReport
from .domain import (
    REQUEST_TYPE_LABELS, STATUS_LABELS, SUBJECT_TYPE_LABELS,
    RequesterKind, RequestType, SubjectType,
)

#: Поддерживаемые формы плейсхолдеров: {{ФИО}}, {ФИО}, [ФИО], <ФИО>.
PLACEHOLDER_RE = re.compile(
    r"\{\{\s*([^{}]{1,80}?)\s*\}\}"      # {{ ... }}
    r"|\{\s*([А-ЯA-Z_][^{}]{0,79}?)\s*\}"  # { ... }
    r"|\[\s*([А-ЯA-Z_][^\[\]]{0,79}?)\s*\]"  # [ ... ]
    r"|<\s*([А-ЯA-Z_][^<>]{0,79}?)\s*>"  # < ... >
)


def find_placeholders(body: str) -> list[str]:
    """Список уникальных плейсхолдеров в порядке появления."""
    seen: list[str] = []
    for m in PLACEHOLDER_RE.finditer(body or ""):
        name = next((g for g in m.groups() if g), "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def _norm_key(name: str) -> str:
    return re.sub(r"[\s_.\-]+", "_", (name or "").strip().lower())


#: Синонимы плейсхолдеров -> канонический ключ контекста.
ALIASES: dict[str, str] = {}


def _alias(canonical: str, *names: str) -> None:
    for n in names:
        ALIASES[_norm_key(n)] = canonical


_alias("requester_name", "фио", "фио_заявителя", "заявитель", "имя", "фио заявителя",
       "субъект", "фио субъекта", "адресат", "получатель")
_alias("requester_email", "email", "почта", "email заявителя", "адрес электронной почты",
       "e_mail", "эл_почта")
_alias("reg_number", "номер", "рег_номер", "номер обращения", "исх_номер", "исходящий номер",
       "номер_ответа", "рег номер")
_alias("received_date", "дата обращения", "дата запроса", "дата_обращения", "дата поступления",
       "дата письма", "дата вх")
_alias("today", "дата", "дата ответа", "текущая дата", "дата_ответа", "сегодня")
_alias("due_date", "срок", "срок ответа", "крайний срок", "дата_срока", "срок_ответа")
_alias("legal_entity", "юрлицо", "организация", "компания", "оператор", "наименование оператора",
       "юл", "наименование организации", "общество")
_alias("legal_entity_address", "адрес", "адрес оператора", "юридический адрес", "место нахождения")
_alias("legal_entity_inn", "инн", "инн оператора")
_alias("rkn_operator_number", "номер оператора", "регистрационный номер оператора",
       "номер в реестре операторов", "рег номер оператора")
_alias("dpo_name", "dpo", "ответственный", "ответственное лицо", "ответственный за обработку",
       "подпись", "подписант", "фио ответственного")
_alias("dpo_email", "email dpo", "почта dpo", "контакт", "контактный email")
_alias("service", "сервис", "бизнес-процесс", "процесс", "система", "информационная система",
       "испдн", "продукт")
_alias("request_type", "тип обращения", "тип запроса", "предмет обращения", "суть обращения")
_alias("subject_type", "вид субъекта", "категория субъекта", "статус заявителя")
_alias("legal_basis", "норма", "основание", "правовое основание", "статья")
_alias("summary", "суть", "краткое содержание", "содержание обращения")
_alias("inbox_email", "ящик", "адрес получения", "почта получения")
_alias("due_date_extended", "срок с продлением", "продленный срок")

# Отдельный плейсхолдер на каждую контрольную точку. Без этого универсальный
# {{СРОК}} подставлял срок ответа (10 рабочих дней) в предложение про
# тридцатидневное уничтожение — формально неверный ответ субъекту.
_alias("due_ANSWER", "срок ответа", "срок предоставления сведений")
_alias("due_ANSWER_RKN", "срок ответа в ркн", "срок ответа роскомнадзору")
_alias("due_NOTIFY", "срок уведомления", "срок уведомления субъекта")
_alias("due_STOP_PROCESSING", "срок прекращения обработки", "срок прекращения")
_alias("due_ERASE", "срок уничтожения", "срок удаления")
_alias("due_RECTIFY", "срок уточнения", "срок исправления")
_alias("due_DECIDE", "срок решения")
_alias("due_STOP_UNLAWFUL", "срок прекращения неправомерной обработки")
_alias("due_ERASE_IF_IMPOSSIBLE", "срок уничтожения при невозможности")
_alias("due_EXECUTE", "срок исполнения предписания", "срок исполнения")
_alias("due_REPORT", "срок отчета об исполнении")
_alias("due_REVIEW", "срок рассмотрения возражения")
_alias("due_NOTIFY_24H", "срок уведомления об инциденте")
_alias("due_NOTIFY_72H", "срок результатов расследования")


def build_context(
    *,
    request,
    deadlines: DeadlineReport | None,
    legal_entity=None,
    service=None,
    today: date | None = None,
) -> dict[str, str]:
    """Значения для подстановки в шаблон."""
    today = today or date.today()

    def fmt(d) -> str:
        if isinstance(d, datetime):
            d = d.date()
        return d.strftime("%d.%m.%Y") if isinstance(d, date) else ""

    rt = RequestType(request.request_type)
    st = SubjectType(request.subject_type)
    primary = deadlines.primary if deadlines else None

    ctx: dict[str, str] = {
        "requester_name": request.requester_name or "",
        "requester_email": request.requester_email or "",
        "reg_number": request.reg_number or "",
        "received_date": fmt(request.received_at),
        "today": fmt(today),
        "due_date": fmt(primary.due_date) if primary and primary.due_date else "",
        "legal_entity": (legal_entity.name if legal_entity else "")
                        or request.legal_entity_mentioned or "",
        "legal_entity_address": legal_entity.address if legal_entity else "",
        "legal_entity_inn": legal_entity.inn if legal_entity else "",
        "rkn_operator_number": legal_entity.rkn_operator_number if legal_entity else "",
        "dpo_name": (legal_entity.dpo_name if legal_entity else "") or request.assignee or "",
        "dpo_email": (legal_entity.dpo_email if legal_entity else "") or request.inbox_email or "",
        "service": (service.name if service else "") or request.service_mentioned or "",
        "request_type": REQUEST_TYPE_LABELS.get(rt, rt.value),
        "subject_type": SUBJECT_TYPE_LABELS.get(st, st.value),
        "legal_basis": primary.legal_ref if primary else "",
        "summary": request.summary or "",
        "inbox_email": request.inbox_email or "",
        "due_date_extended": (fmt(primary.extended_due_date)
                              if primary and primary.extended_due_date else ""),
    }

    # Дата каждой контрольной точки — доступна как отдельный плейсхолдер.
    for d in (deadlines.deadlines if deadlines else []):
        if d.unit == "HOURS" and d.due_at:
            ctx[f"due_{d.code}"] = d.due_at.strftime("%d.%m.%Y %H:%M")
        elif d.due_date:
            ctx[f"due_{d.code}"] = fmt(d.due_date)

    # «Срок ответа» есть не у всех типов отдельной точкой (у отзыва согласия это
    # NOTIFY, у предписания — EXECUTE). Чтобы плейсхолдер не оставался пустым,
    # он всегда указывает на основной срок обращения.
    for key in ("due_ANSWER", "due_ANSWER_RKN"):
        if not ctx.get(key) and ctx.get("due_date"):
            ctx[key] = ctx["due_date"]

    return {k: v for k, v in ctx.items() if v}


def fill(body: str, ctx: dict[str, str]) -> tuple[str, list[str]]:
    """Подставить значения. Возвращает текст и список незаполненных плейсхолдеров."""
    unresolved: list[str] = []

    def repl(m: re.Match) -> str:
        raw = next((g for g in m.groups() if g), "").strip()
        key = ALIASES.get(_norm_key(raw))
        value = ctx.get(key) if key else None
        if value:
            return value
        if raw not in unresolved:
            unresolved.append(raw)
        # Оставляем плейсхолдер видимым — так его нельзя не заметить перед отправкой.
        return f"[{raw}]"

    return PLACEHOLDER_RE.sub(repl, body or ""), unresolved


# --------------------------------------------------------------------------- #
#  Подбор шаблона
# --------------------------------------------------------------------------- #

@dataclass
class TemplateMatch:
    template_id: int
    title: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"template_id": self.template_id, "title": self.title,
                "score": round(self.score, 2), "reasons": self.reasons}


def rank_templates(request, templates: list) -> list[TemplateMatch]:
    """Отранжировать типовые ответы по применимости к обращению."""
    out: list[TemplateMatch] = []
    for t in templates:
        if not t.is_active:
            continue
        score, reasons = 0.0, []
        types = t.request_types or []
        if types:
            if request.request_type in types:
                score += 10
                reasons.append("совпал тип обращения")
            else:
                continue  # шаблон явно для других типов
        else:
            score += 1
            reasons.append("универсальный шаблон")

        subs = t.subject_types or []
        if subs:
            if request.subject_type in subs:
                score += 4
                reasons.append("совпал вид субъекта")
            elif request.subject_type != SubjectType.UNKNOWN.value:
                score -= 2

        kinds = t.requester_kinds or []
        if kinds:
            if request.requester_kind in kinds:
                score += 3
                reasons.append("совпал тип заявителя")
            else:
                score -= 3

        if t.legal_entity_id:
            if t.legal_entity_id == request.legal_entity_id:
                score += 3
                reasons.append("шаблон этого юрлица")
            else:
                score -= 4

        score += min(t.usage_count, 10) * 0.1
        if score > 0:
            out.append(TemplateMatch(t.id, t.title, score, reasons))
    out.sort(key=lambda m: -m.score)
    return out


# --------------------------------------------------------------------------- #
#  Чек-лист перед отправкой
# --------------------------------------------------------------------------- #

def build_checklist(request, deadlines: DeadlineReport | None, flags: list) -> list[dict]:
    """Что обязательно проверить перед отправкой ответа."""
    items: list[dict] = []

    def add(text: str, ref: str = "", critical: bool = False) -> None:
        items.append({"text": text, "ref": ref, "critical": critical, "done": False})

    rt = RequestType(request.request_type)
    rk = RequesterKind(request.requester_kind)

    if not request.identity_confirmed_at and rk in (
        RequesterKind.SUBJECT, RequesterKind.SUBJECT_REPRESENTATIVE, RequesterKind.UNKNOWN
    ) and rt not in (RequestType.CONSENT_WITHDRAWAL, RequestType.STOP_MARKETING):
        add("Подтвердить личность заявителя: реквизиты документа, удостоверяющего личность, "
            "и сведения, подтверждающие участие в отношениях с оператором.",
            "ч. 4 ст. 14 ФЗ-152", critical=True)

    if rk is RequesterKind.SUBJECT_REPRESENTATIVE:
        add("Проверить доверенность: срок действия и прямое полномочие на получение "
            "персональных данных доверителя.", "ч. 3 ст. 14 ФЗ-152", critical=True)

    if rt in (RequestType.ACCESS, RequestType.CONFIRM_PROCESSING):
        add("Включить в ответ все сведения по перечню: подтверждение факта обработки, "
            "правовые основания и цели, способы обработки, наименование и адрес оператора, "
            "обрабатываемые данные и источник их получения, сроки обработки, порядок "
            "реализации прав, сведения о трансграничной передаче и о лицах, обрабатывающих "
            "данные по поручению.", "ч. 7 ст. 14 ФЗ-152", critical=True)
        add("Проверить основания для ограничения права на доступ (гостайна, "
            "оперативно-розыскная деятельность, права третьих лиц). При отказе — "
            "мотивированный ответ со ссылкой на конкретный пункт.", "ч. 8 ст. 14 ФЗ-152")

    if rt is RequestType.CONSENT_WITHDRAWAL:
        add("Разделить обработку на прекращаемую и продолжающуюся: указать, какие данные "
            "продолжают обрабатываться и на каком основании из ч. 1 ст. 6.",
            "ч. 1 ст. 6, ч. 5 ст. 21 ФЗ-152", critical=True)

    if rt is RequestType.ERASURE:
        add("Сверить со сроками обязательного хранения (кадровые документы, бухгалтерия, "
            "ФЗ-115). Что нельзя уничтожить — блокировать и объяснить основание.",
            "ч. 4 ст. 21 ФЗ-152", critical=True)
        add("Оформить акт об уничтожении и выгрузку из журнала регистрации событий в ИСПДн.",
            "Приказ Роскомнадзора от 28.10.2022 № 179")

    if rt in (RequestType.RECTIFICATION, RequestType.BLOCKING, RequestType.UNLAWFUL_PROCESSING):
        add("Зафиксировать факт блокирования данных на период проверки и дату его начала.",
            "ч. 1 ст. 21 ФЗ-152", critical=True)
        add("Уведомить третьих лиц, которым данные передавались, о внесённых изменениях "
            "или об уничтожении.", "ч. 4 ст. 21 ФЗ-152")

    if rt in (RequestType.RKN_INFO_REQUEST, RequestType.RKN_SUBJECT_COMPLAINT,
              RequestType.RKN_ORDER):
        add("Ответ оформить на бланке за подписью уполномоченного лица, с исходящим "
            "номером и приложением подтверждающих документов.", "", critical=True)
        add("Проверить срок, указанный в самом письме Роскомнадзора, — он приоритетнее "
            "расчётного.", "ст. 20 ч. 4 ФЗ-152", critical=True)

    if request.extension_applied:
        add("Направить мотивированное уведомление о продлении срока С УКАЗАНИЕМ ПРИЧИН — "
            "без него продление неправомерно.", "ст. 20 ч. 1, ч. 4 ФЗ-152", critical=True)

    for f in flags:
        if getattr(f, "level", "") == "BLUE" and not getattr(f, "resolved_at", None):
            add(f"Снять спорный вопрос: {getattr(f, 'reason', '')[:220]}", "", critical=False)

    if deadlines:
        for d in deadlines.immediate_actions:
            add(f"{d.title} — обязанность возникает с момента обращения, не откладывается.",
                d.legal_ref, critical=True)

    add("Направить ответ способом, позволяющим подтвердить получение, и сохранить "
        "доказательство отправки.", "")
    return items


# --------------------------------------------------------------------------- #
#  Резервный драфт без ИИ и без шаблона
# --------------------------------------------------------------------------- #

FALLBACK_BODY = """{legal_entity_line}

{addressee}

Исх. № {reg_number} от {today}
На № б/н от {received_date}

Уважаем{ending} {requester_name}!

Рассмотрев Ваше обращение, поступившее {received_date} на адрес {inbox_email}, \
сообщаем следующее.

Обращение квалифицировано как: {request_type}.
Применимая норма: {legal_basis}.
Срок предоставления ответа: {due_date}.

[ИЗЛОЖИТЬ СУЩЕСТВО ОТВЕТА]

{type_block}

Настоящий ответ подготовлен в соответствии с Федеральным законом от 27.07.2006 \
№ 152-ФЗ «О персональных данных».

{dpo_name}
{dpo_email}
"""

TYPE_BLOCKS: dict[RequestType, str] = {
    RequestType.ACCESS:
        "В соответствии с частью 7 статьи 14 Федерального закона № 152-ФЗ ниже приведены "
        "сведения, касающиеся обработки Ваших персональных данных:\n"
        "1) подтверждение факта обработки: [указать];\n"
        "2) правовые основания и цели обработки: [указать];\n"
        "3) применяемые оператором способы обработки: [указать];\n"
        "4) наименование и место нахождения оператора: [указать];\n"
        "5) обрабатываемые персональные данные и источник их получения: [указать];\n"
        "6) сроки обработки, в том числе сроки хранения: [указать];\n"
        "7) порядок осуществления прав, предусмотренных Федеральным законом № 152-ФЗ;\n"
        "8) сведения об осуществлённой или предполагаемой трансграничной передаче: [указать];\n"
        "9) сведения о лице, осуществляющем обработку по поручению оператора: [указать].",
    RequestType.CONSENT_WITHDRAWAL:
        "Ваше согласие на обработку персональных данных отозвано. Обработка персональных "
        "данных, осуществлявшаяся на основании согласия, прекращена [указать перечень].\n"
        "Одновременно сообщаем, что обработка следующих персональных данных продолжается "
        "на иных правовых основаниях, предусмотренных частью 1 статьи 6 Федерального "
        "закона № 152-ФЗ: [указать данные и основание].",
    RequestType.ERASURE:
        "Персональные данные, обработка которых более не требуется для заявленных целей, "
        "уничтожены [указать перечень и дату]. Факт уничтожения подтверждается актом "
        "от [дата].\n"
        "Персональные данные, подлежащие обязательному хранению в силу закона "
        "[указать норму], уничтожению не подлежат; их обработка ограничена хранением.",
    RequestType.RECTIFICATION:
        "На период проверки представленных Вами сведений обработка соответствующих "
        "персональных данных была ограничена (блокирована). По результатам проверки "
        "персональные данные уточнены: [указать, что изменено]. Блокирование снято.",
    RequestType.STOP_MARKETING:
        "Обработка Ваших персональных данных в целях продвижения товаров, работ и услуг "
        "на рынке прекращена. Ваши контактные данные исключены из соответствующих "
        "рассылок [указать каналы].",
    RequestType.RKN_INFO_REQUEST:
        "Во исполнение запроса представляем запрошенные сведения:\n"
        "1) [пункт запроса] — [ответ];\n"
        "2) [пункт запроса] — [ответ].\n"
        "Приложение: [перечень документов на __ л. в __ экз.].",
    RequestType.UNLAWFUL_PROCESSING:
        "По изложенным Вами доводам проведена проверка. На период проверки обработка "
        "соответствующих персональных данных была блокирована.\n"
        "По результатам проверки установлено: [изложить]. Приняты следующие меры: [указать].",
}


def fallback_draft(request, ctx: dict[str, str]) -> str:
    """Структурная заготовка, когда типовой ответ не загружен и ИИ недоступен."""
    rt = RequestType(request.request_type)
    rk = RequesterKind(request.requester_kind)
    if rk is RequesterKind.RKN:
        addressee = "В Роскомнадзор\n(территориальный орган — указать)"
        ending = "ые коллеги"
        name = ""
    else:
        addressee = ctx.get("requester_name", "") or "[ФИО заявителя]"
        ending = "ый(ая)"
        name = ctx.get("requester_name", "") or "[ФИО заявителя]"

    body = FALLBACK_BODY.format(
        legal_entity_line=ctx.get("legal_entity", "[Наименование оператора]"),
        addressee=addressee,
        reg_number=ctx.get("reg_number", "[номер]"),
        today=ctx.get("today", ""),
        received_date=ctx.get("received_date", "[дата]"),
        ending=ending,
        requester_name=name,
        inbox_email=ctx.get("inbox_email", "[адрес]"),
        request_type=ctx.get("request_type", ""),
        legal_basis=ctx.get("legal_basis", "") or "—",
        due_date=ctx.get("due_date", "") or "—",
        type_block=TYPE_BLOCKS.get(rt, ""),
        dpo_name=ctx.get("dpo_name", "[Ответственный за организацию обработки "
                                     "персональных данных]"),
        dpo_email=ctx.get("dpo_email", ""),
    )
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def build_llm_context(request, deadlines: DeadlineReport | None, flags: list,
                      ctx: dict[str, str]) -> str:
    """Сводка обращения для передачи модели при генерации драфта."""
    lines = [
        "=== КАРТОЧКА ОБРАЩЕНИЯ ===",
        f"Регистрационный номер: {request.reg_number}",
        f"Поступило: {ctx.get('received_date', '')} на ящик {request.inbox_email or '—'}",
        f"Тип обращения: {ctx.get('request_type', '')}",
        f"Заявитель: {RequesterKind(request.requester_kind).value}, "
        f"вид субъекта: {ctx.get('subject_type', '')}",
        f"ФИО заявителя: {request.requester_name or '—'}",
        f"Юридическое лицо: {ctx.get('legal_entity', '—')}",
        f"Сервис / бизнес-процесс: {ctx.get('service', '—')}",
        f"Статус: {STATUS_LABELS.get(request.status, request.status)}",
    ]
    if deadlines:
        lines.append("\n=== РАССЧИТАННЫЕ СРОКИ (использовать как есть) ===")
        for d in deadlines.deadlines:
            when = d.due_at.strftime("%d.%m.%Y %H:%M") if d.due_at and d.unit == "HOURS" \
                else (d.due_date.strftime("%d.%m.%Y") if d.due_date else "—")
            lines.append(f"- {d.title}: {when} ({d.legal_ref})")
    open_blue = [f for f in flags if getattr(f, "level", "") == "BLUE"
                 and not getattr(f, "resolved_at", None)]
    if open_blue:
        lines.append("\n=== СПОРНЫЕ ВОПРОСЫ (учесть в тексте) ===")
        for f in open_blue:
            lines.append(f"- {getattr(f, 'reason', '')}")
    lines.append("\n=== ТЕКСТ ОБРАЩЕНИЯ ===")
    lines.append((request.body_text or "")[:15000])
    return "\n".join(lines)
