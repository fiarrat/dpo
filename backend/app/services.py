"""Оркестрация: приём обращения, разбор, пересчёт сроков, ведение журнала."""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import classifier as clf
from . import llm
from .deadlines import DeadlineReport, compute
from .domain import (
    Channel, Flag, RequesterKind, RequestType, Status, SubjectType, TERMINAL_STATUSES,
)
from .extraction import ExtractionResult, extract, save_upload
from .models import (
    Attachment, Event, Inbox, LegalEntity, Request, RequestFlag, Service, utcnow,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Вспомогательное
# --------------------------------------------------------------------------- #

def next_reg_number(session: Session, when: datetime | None = None) -> str:
    """Регистрационный номер вида ПД-2026-000123, сквозной в пределах года."""
    year = (when or utcnow()).year
    prefix = f"ПД-{year}-"
    last = session.scalar(
        select(func.max(Request.reg_number)).where(Request.reg_number.like(prefix + "%"))
    )
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:06d}"


def log_event(session: Session, request: Request, kind: str, message: str,
              actor: str = "system", payload: dict | None = None) -> None:
    session.add(Event(request_id=request.id, kind=kind, message=message,
                      actor=actor, payload=payload or {}))


def _enum_or(value, enum_cls, default):
    try:
        return enum_cls(value)
    except (ValueError, TypeError):
        return default


# --------------------------------------------------------------------------- #
#  Пересчёт сроков и денормализация
# --------------------------------------------------------------------------- #

def deadline_report(request: Request, now: datetime | None = None) -> DeadlineReport:
    return compute(
        _enum_or(request.request_type, RequestType, RequestType.UNCLASSIFIED),
        request.received_at,
        status=_enum_or(request.status, Status, Status.NEW),
        identity_confirmed_at=request.identity_confirmed_at,
        manual_due_date=request.manual_due_date,
        extension_applied=request.extension_applied,
        now=now,
    )


def recalculate(session: Session, request: Request, now: datetime | None = None
                ) -> DeadlineReport:
    """Пересчитать срок и обновить денормализованные поля для фильтров реестра."""
    report = deadline_report(request, now=now)
    request.due_date = report.due_date
    request.urgency = report.urgency
    flags = session.scalars(
        select(RequestFlag).where(RequestFlag.request_id == request.id)
    ).all() if request.id else request.flags
    request.has_red_flag = any(f.level == Flag.RED.value and not f.resolved_at for f in flags)
    request.has_blue_flag = any(f.level == Flag.BLUE.value and not f.resolved_at for f in flags)
    return report


def recalculate_all(session: Session) -> int:
    """Ночной пересчёт: срочность зависит от текущей даты, а не только от правок."""
    requests = session.scalars(select(Request)).all()
    for r in requests:
        recalculate(session, r)
    session.flush()
    return len(requests)


# --------------------------------------------------------------------------- #
#  Флажки
# --------------------------------------------------------------------------- #

def sync_flags(session: Session, request: Request,
               proposals: list[clf.FlagProposal], source: str = "RULES") -> None:
    """
    Синхронизировать автоматические флажки.

    Флажки, снятые человеком (resolved_at), и флажки, поставленные вручную,
    не трогаются — иначе повторный разбор затирал бы решения DPO.
    """
    existing = session.scalars(
        select(RequestFlag).where(RequestFlag.request_id == request.id)
    ).all() if request.id else list(request.flags)

    auto = {f.code: f for f in existing if f.source != "MANUAL"}
    proposed_codes = {p.code for p in proposals}

    for p in proposals:
        cur = auto.get(p.code)
        if cur is None:
            session.add(RequestFlag(request_id=request.id, level=p.level.value,
                                    code=p.code, reason=p.reason, source=source))
        elif not cur.resolved_at:
            cur.level, cur.reason, cur.source = p.level.value, p.reason, source

    # Снятые правилами флажки убираем, но только неразобранные человеком.
    for code, f in auto.items():
        if code not in proposed_codes and not f.resolved_at:
            session.delete(f)
    session.flush()


# --------------------------------------------------------------------------- #
#  Разбор обращения
# --------------------------------------------------------------------------- #

def analyze_request(session: Session, request: Request, *, use_llm: bool = True,
                    overwrite_manual: bool = False) -> dict:
    """
    Разобрать обращение: тип, заявитель, вид субъекта, ЮЛ, сервис, флажки.

    Поля, которые человек уже правил вручную (classified_by == 'MANUAL'),
    по умолчанию не перезаписываются.
    """
    attachments_text = "\n\n".join(
        a.extracted_text for a in request.attachments if a.extracted_text
    )
    inbox = session.get(Inbox, request.inbox_id) if request.inbox_id else None

    cls = clf.classify(
        body=request.body_text or "",
        subject_line=request.subject_line or "",
        from_email=request.requester_email or "",
        inbox_purpose=(inbox.purpose if inbox else ""),
        attachments_text=attachments_text,
    )

    llm_info: dict = {"used": False}
    if use_llm and llm.available():
        res = llm.analyze(
            subject_line=request.subject_line or "",
            body=request.body_text or "",
            from_email=request.requester_email or "",
            inbox_email=request.inbox_email or "",
            attachments_text=attachments_text,
        )
        llm_info = {"used": res.ok, "error": res.error, "model": res.model}
        if res.ok:
            cls = merge_llm(cls, res.data)

    manual = request.classified_by == "MANUAL" and not overwrite_manual
    if not manual:
        request.request_type = cls.request_type.value
        request.secondary_types = [t.value for t in cls.secondary_types]
        request.requester_kind = cls.requester_kind.value
        request.subject_type = cls.subject_type.value
        request.classified_by = "LLM" if llm_info.get("used") else "RULES"
        request.classification_confidence = cls.confidence

    request.summary = cls.summary
    request.classification = {**cls.to_dict(), "llm": llm_info}

    full_text = "\n".join(filter(None, [
        request.subject_line, request.body_text, attachments_text
    ]))

    # Юридическое лицо: приоритет у ящика, затем распознавание в тексте.
    entities = session.scalars(select(LegalEntity).where(LegalEntity.is_active)).all()
    ent_id, ent_name = clf.match_legal_entity(full_text, entities)
    if ent_name:
        request.legal_entity_mentioned = ent_name
    elif cls.legal_entity_mentioned:
        request.legal_entity_mentioned = cls.legal_entity_mentioned
    if request.legal_entity_id is None:
        request.legal_entity_id = ent_id or (inbox.legal_entity_id if inbox else None)

    # Сервис / бизнес-процесс.
    services = session.scalars(select(Service).where(Service.is_active)).all()
    svc_id, svc_name = clf.match_service(
        full_text, services, _enum_or(request.subject_type, SubjectType, SubjectType.UNKNOWN)
    )
    if request.service_id is None and svc_id:
        request.service_id = svc_id
    if svc_name and not request.service_mentioned:
        request.service_mentioned = svc_name
    elif cls.service_mentioned and not request.service_mentioned:
        request.service_mentioned = cls.service_mentioned

    # Что заполнено автоматом и ещё не подтверждено человеком.
    unconfirmed = []
    if request.classified_by != "MANUAL":
        unconfirmed.append("request_type")
        if request.subject_type == SubjectType.UNKNOWN.value:
            unconfirmed.append("subject_type")
        if not request.legal_entity_id:
            unconfirmed.append("legal_entity")
        if not request.service_id:
            unconfirmed.append("service")
    request.unconfirmed_fields = unconfirmed

    # Реквизиты из текста — заполняем только пустые поля.
    ex = cls.extracted
    if not request.requester_name and ex.get("requester_name"):
        request.requester_name = ex["requester_name"]
    if not request.requester_phone and ex.get("phones"):
        request.requester_phone = ex["phones"][0]

    session.flush()
    sync_flags(session, request, cls.flags,
               source="LLM" if llm_info.get("used") else "RULES")
    report = recalculate(session, request)

    log_event(session, request, "ANALYZED",
              f"Автоматический разбор: {cls.request_type.value} "
              f"(уверенность {cls.confidence:.0%}), "
              f"{'ИИ + правила' if llm_info.get('used') else 'правила'}.",
              payload={"confidence": cls.confidence, "llm": llm_info})

    return {"classification": cls.to_dict(), "deadlines": report.to_dict(), "llm": llm_info}


def merge_llm(cls: clf.Classification, data: dict) -> clf.Classification:
    """
    Соединить результат правил и модели.

    Модель может переопределить тип, только если она уверена сильнее правил.
    Флажки объединяются: ИИ умеет находить спорность, которую правила не ловят.
    """
    llm_conf = float(data.get("confidence") or 0)
    llm_type = _enum_or(data.get("request_type"), RequestType, None)

    if llm_type and (llm_conf > cls.confidence + 0.1 or
                     cls.request_type is RequestType.UNCLASSIFIED):
        cls.signals.append(clf.Signal(
            "TYPE", llm_type.value, llm_conf,
            f"ИИ: {data.get('summary', '')[:120]}", "llm"))
        cls.request_type = llm_type
        cls.confidence = max(cls.confidence, llm_conf)

    llm_kind = _enum_or(data.get("requester_kind"), RequesterKind, None)
    if llm_kind and cls.requester_kind is RequesterKind.UNKNOWN:
        cls.requester_kind = llm_kind
    llm_subj = _enum_or(data.get("subject_type"), SubjectType, None)
    if llm_subj and cls.subject_type is SubjectType.UNKNOWN:
        cls.subject_type = llm_subj

    for t in (data.get("secondary_types") or []):
        st = _enum_or(t, RequestType, None)
        if st and st is not cls.request_type and st not in cls.secondary_types:
            cls.secondary_types.append(st)

    have = {f.code for f in cls.flags}
    for item in (data.get("red_flags") or []):
        code = f"LLM_{item.get('code', 'RED')}"
        if code not in have:
            cls.flags.append(clf.FlagProposal(Flag.RED, code, item.get("reason", "")))
    for item in (data.get("blue_flags") or []):
        code = f"LLM_{item.get('code', 'BLUE')}"
        if code not in have:
            cls.flags.append(clf.FlagProposal(Flag.BLUE, code, item.get("reason", "")))

    if data.get("summary"):
        cls.summary = data["summary"]
    if data.get("legal_entity_mentioned"):
        cls.legal_entity_mentioned = data["legal_entity_mentioned"]
    if data.get("service_mentioned"):
        cls.service_mentioned = data["service_mentioned"]
    for key in ("requester_name", "deadline_in_document"):
        if data.get(key):
            cls.extracted[key] = data[key]
    if data.get("key_points"):
        cls.extracted["key_points"] = data["key_points"]
    return cls


# --------------------------------------------------------------------------- #
#  Приём обращения
# --------------------------------------------------------------------------- #

def resolve_inbox(session: Session, inbox_email: str) -> Inbox | None:
    if not inbox_email:
        return None
    return session.scalar(select(Inbox).where(func.lower(Inbox.email) == inbox_email.lower()))


def create_request(
    session: Session,
    *,
    inbox_email: str = "",
    requester_email: str = "",
    requester_name: str = "",
    subject_line: str = "",
    body_text: str = "",
    received_at: datetime | None = None,
    channel: Channel = Channel.EMAIL,
    message_id: str = "",
    legal_entity_id: int | None = None,
    service_id: int | None = None,
    files: list[tuple[str, bytes]] | None = None,
    analyze: bool = True,
    use_llm: bool = True,
    actor: str = "system",
) -> tuple[Request, dict]:
    """Создать обращение, разобрать вложения и запустить классификацию."""
    received_at = received_at or utcnow()
    inbox = resolve_inbox(session, inbox_email)

    req = Request(
        reg_number=next_reg_number(session, received_at),
        channel=channel.value,
        received_at=received_at,
        registered_at=utcnow(),
        inbox_id=inbox.id if inbox else None,
        inbox_email=inbox_email or (inbox.email if inbox else ""),
        message_id=message_id,
        requester_email=requester_email,
        requester_name=requester_name,
        subject_line=subject_line,
        body_text=body_text,
        legal_entity_id=legal_entity_id or (inbox.legal_entity_id if inbox else None),
        service_id=service_id,
        status=Status.NEW.value,
    )
    session.add(req)
    session.flush()

    warnings: list[str] = []
    for filename, data in (files or []):
        att, res = attach_file(session, req, filename, data)
        if res.error:
            warnings.append(f"{filename}: {res.error}")
        warnings.extend(f"{filename}: {w}" for w in res.warnings)

    log_event(session, req, "CREATED",
              f"Обращение зарегистрировано ({channel.value}), "
              f"ящик: {req.inbox_email or 'не указан'}.", actor=actor)

    result: dict = {"warnings": warnings}
    if analyze:
        result.update(analyze_request(session, req, use_llm=use_llm))
    else:
        result["deadlines"] = recalculate(session, req).to_dict()

    session.flush()
    return req, result


def attach_file(session: Session, request: Request | None, filename: str, data: bytes
                ) -> tuple[Attachment, ExtractionResult]:
    """Сохранить вложение и извлечь из него текст."""
    path, digest = save_upload(filename, data)
    res = extract(filename, data)

    att = Attachment(
        request_id=request.id if request else None,
        filename=filename,
        size_bytes=len(data),
        sha256=digest,
        stored_path=str(path),
        extracted_text=res.text,
        extraction_method=res.method,
        extraction_error=res.error,
        page_count=res.page_count,
        char_count=res.char_count,
        needs_review=res.needs_review,
    )
    session.add(att)
    session.flush()
    if request is not None:
        log_event(session, request, "ATTACHMENT",
                  f"Вложение «{filename}»: {res.method}, извлечено {res.char_count} симв."
                  + (f" Ошибка: {res.error}" if res.error else ""))
    return att, res


# --------------------------------------------------------------------------- #
#  Смена статуса
# --------------------------------------------------------------------------- #

def set_status(session: Session, request: Request, new_status: Status,
               actor: str = "user", note: str = "") -> None:
    old = request.status
    request.status = new_status.value
    now = utcnow()
    if new_status is Status.ANSWERED and not request.answered_at:
        request.answered_at = now
    if new_status in (Status.CLOSED, Status.REJECTED, Status.NOT_APPLICABLE) \
            and not request.closed_at:
        request.closed_at = now
    if new_status not in TERMINAL_STATUSES:
        request.closed_at = None
        if new_status is not Status.ANSWERED:
            request.answered_at = None
    log_event(session, request, "STATUS",
              f"Статус: {old} → {new_status.value}." + (f" {note}" if note else ""),
              actor=actor)
    recalculate(session, request)
