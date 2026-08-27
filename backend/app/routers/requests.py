"""Реестр обращений: список с фильтрами, карточка, правки, флажки, вложения."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi import Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import services as svc
from ..config import settings
from ..db import get_session
from ..domain import (
    NON_PD_TYPES, REQUEST_TYPE_LABELS, RKN_TYPES, STATUS_LABELS, SUBJECT_TYPE_LABELS,
    URGENCY_ORDER, Channel, Flag, RequestType, Status, SubjectType, Urgency,
)
from ..models import Attachment, Draft, LegalEntity, Request, RequestFlag, Service, utcnow
from ..schemas import (
    AttachmentDetail, AttachmentOut, FlagIn, FlagOut, FlagResolve, RequestCreate,
    RequestDetail, RequestListItem, RequestPage, RequestUpdate,
)

router = APIRouter(prefix="/api/requests", tags=["requests"])

SORTABLE = {
    "received_at": Request.received_at,
    "due_date": Request.due_date,
    "reg_number": Request.reg_number,
    "status": Request.status,
}


def _get(db: Session, request_id: int) -> Request:
    stmt = (
        select(Request)
        .where(Request.id == request_id)
        .options(
            selectinload(Request.flags), selectinload(Request.attachments),
            selectinload(Request.drafts), selectinload(Request.events),
        )
    )
    obj = db.scalar(stmt)
    if not obj:
        raise HTTPException(404, "Обращение не найдено")
    return obj


# --------------------------------------------------------------------------- #
#  Список
# --------------------------------------------------------------------------- #

@router.get("", response_model=RequestPage)
def list_requests(
    q: str = "",
    urgency: list[str] = Query(default=[]),
    request_type: list[str] = Query(default=[]),
    type_group: list[str] = Query(default=[]),
    subject_type: list[str] = Query(default=[]),
    requester_kind: list[str] = Query(default=[]),
    status: list[str] = Query(default=[]),
    inbox_email: list[str] = Query(default=[]),
    legal_entity_id: list[int] = Query(default=[]),
    service_id: list[int] = Query(default=[]),
    flag: str = "",
    overdue: bool = False,
    open_only: bool = False,
    due_from: date | None = None,
    due_to: date | None = None,
    received_from: date | None = None,
    received_to: date | None = None,
    assignee: str = "",
    sort: str = "urgency",
    order: str = "asc",
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_session),
) -> RequestPage:
    """
    Реестр с фильтрами. Сортировка по срочности выполняется в Python: порядок
    задан смысловой шкалой (просрочено → сегодня → критично → …), а не алфавитом.
    """
    stmt = select(Request)

    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(
            func.lower(Request.reg_number).like(like),
            func.lower(Request.subject_line).like(like),
            func.lower(Request.body_text).like(like),
            func.lower(Request.requester_name).like(like),
            func.lower(Request.requester_email).like(like),
            func.lower(Request.legal_entity_mentioned).like(like),
            func.lower(Request.service_mentioned).like(like),
            func.lower(Request.summary).like(like),
        ))
    if urgency:
        stmt = stmt.where(Request.urgency.in_(urgency))
    types = list(request_type)
    for group in type_group:
        if group == "RKN":
            types += [t.value for t in RKN_TYPES]
        elif group == "NON_PD":
            types += [t.value for t in NON_PD_TYPES]
        elif group == "SUBJECT":
            from ..domain import SUBJECT_TYPES_OF_REQUEST
            types += [t.value for t in SUBJECT_TYPES_OF_REQUEST]
    if types:
        stmt = stmt.where(Request.request_type.in_(sorted(set(types))))
    if subject_type:
        stmt = stmt.where(Request.subject_type.in_(subject_type))
    if requester_kind:
        stmt = stmt.where(Request.requester_kind.in_(requester_kind))
    if status:
        stmt = stmt.where(Request.status.in_(status))
    if inbox_email:
        stmt = stmt.where(Request.inbox_email.in_(inbox_email))
    if legal_entity_id:
        stmt = stmt.where(Request.legal_entity_id.in_(legal_entity_id))
    if service_id:
        stmt = stmt.where(Request.service_id.in_(service_id))
    if flag == "red":
        stmt = stmt.where(Request.has_red_flag.is_(True))
    elif flag == "blue":
        stmt = stmt.where(Request.has_blue_flag.is_(True))
    elif flag == "any":
        stmt = stmt.where(or_(Request.has_red_flag.is_(True), Request.has_blue_flag.is_(True)))
    elif flag == "none":
        stmt = stmt.where(Request.has_red_flag.is_(False), Request.has_blue_flag.is_(False))
    if overdue:
        stmt = stmt.where(Request.urgency == Urgency.OVERDUE.value)
    if open_only:
        from ..domain import TERMINAL_STATUSES
        stmt = stmt.where(Request.status.notin_([s.value for s in TERMINAL_STATUSES]))
    if due_from:
        stmt = stmt.where(Request.due_date >= due_from)
    if due_to:
        stmt = stmt.where(Request.due_date <= due_to)
    if received_from:
        stmt = stmt.where(Request.received_at >= datetime.combine(received_from, datetime.min.time()))
    if received_to:
        stmt = stmt.where(Request.received_at <= datetime.combine(received_to, datetime.max.time()))
    if assignee:
        stmt = stmt.where(Request.assignee == assignee)

    rows = list(db.scalars(stmt))

    if sort == "urgency":
        rows.sort(key=lambda r: (
            URGENCY_ORDER.get(Urgency(r.urgency), 9),
            r.due_date or date.max,
            -r.id,
        ), reverse=(order == "desc"))
    else:
        col = SORTABLE.get(sort, Request.received_at)
        name = col.key
        rows.sort(key=lambda r: (getattr(r, name) is None, getattr(r, name) or 0),
                  reverse=(order == "desc"))

    total = len(rows)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 500)
    window = rows[(page - 1) * page_size: page * page_size]

    return RequestPage(
        items=[RequestListItem.model_validate(r) for r in window],
        total=total, page=page, page_size=page_size,
        facets=_facets(rows),
    )


def _facets(rows: list[Request]) -> dict:
    """Счётчики по текущей выборке — показываются на фильтрах."""
    def count(attr):
        out: dict[str, int] = {}
        for r in rows:
            out[getattr(r, attr)] = out.get(getattr(r, attr), 0) + 1
        return out
    return {
        "urgency": count("urgency"),
        "status": count("status"),
        "request_type": count("request_type"),
        "subject_type": count("subject_type"),
        "requester_kind": count("requester_kind"),
        "inbox_email": count("inbox_email"),
        "red": sum(1 for r in rows if r.has_red_flag),
        "blue": sum(1 for r in rows if r.has_blue_flag),
    }


@router.get("/stats")
def stats(db: Session = Depends(get_session)) -> dict:
    """Сводка для верхней панели интерфейса."""
    from ..domain import TERMINAL_STATUSES
    rows = list(db.scalars(select(Request)))
    terminal = {s.value for s in TERMINAL_STATUSES}
    open_rows = [r for r in rows if r.status not in terminal]
    today = date.today()
    return {
        "total": len(rows),
        "open": len(open_rows),
        "overdue": sum(1 for r in open_rows if r.urgency == Urgency.OVERDUE.value),
        "due_today": sum(1 for r in open_rows if r.due_date == today),
        "due_week": sum(1 for r in open_rows
                        if r.due_date and 0 <= (r.due_date - today).days <= 7),
        "red": sum(1 for r in open_rows if r.has_red_flag),
        "blue": sum(1 for r in open_rows if r.has_blue_flag),
        "rkn": sum(1 for r in open_rows if RequestType(r.request_type) in RKN_TYPES),
        "unclassified": sum(1 for r in open_rows
                            if r.request_type == RequestType.UNCLASSIFIED.value),
        "by_status": {s.value: sum(1 for r in rows if r.status == s.value)
                      for s in Status},
        "by_subject_type": {s.value: sum(1 for r in open_rows if r.subject_type == s.value)
                            for s in SubjectType},
        "by_urgency": {u.value: sum(1 for r in open_rows if r.urgency == u.value)
                       for u in Urgency},
    }


@router.get("/export.csv")
def export_csv(db: Session = Depends(get_session)):
    """Выгрузка реестра — для отчётности и приложения к ответам РКН."""
    rows = list(db.scalars(select(Request).order_by(Request.id)))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([
        "Рег. номер", "Дата поступления", "Ящик получения", "Заявитель", "Email",
        "Кто обращается", "Вид субъекта", "Тип обращения", "Юридическое лицо",
        "Сервис / процесс", "Статус", "Срок ответа", "Срочность",
        "Красный флажок", "Синий флажок", "Исполнитель", "Тема",
    ])
    for r in rows:
        w.writerow([
            r.reg_number, r.received_at.strftime("%d.%m.%Y %H:%M"), r.inbox_email,
            r.requester_name, r.requester_email, r.requester_kind,
            SUBJECT_TYPE_LABELS.get(SubjectType(r.subject_type), r.subject_type),
            REQUEST_TYPE_LABELS.get(RequestType(r.request_type), r.request_type),
            r.legal_entity_mentioned, r.service_mentioned,
            STATUS_LABELS.get(Status(r.status), r.status),
            r.due_date.strftime("%d.%m.%Y") if r.due_date else "", r.urgency,
            "да" if r.has_red_flag else "", "да" if r.has_blue_flag else "",
            r.assignee, r.subject_line,
        ])
    data = "﻿" + buf.getvalue()   # BOM — чтобы Excel открыл в UTF-8
    return StreamingResponse(
        io.BytesIO(data.encode("utf-8")), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="requests.csv"'},
    )


# --------------------------------------------------------------------------- #
#  Карточка
# --------------------------------------------------------------------------- #

def _detail(db: Session, obj: Request) -> RequestDetail:
    from .. import drafting
    from ..models import ResponseTemplate

    report = svc.deadline_report(obj)
    entity = db.get(LegalEntity, obj.legal_entity_id) if obj.legal_entity_id else None
    service = db.get(Service, obj.service_id) if obj.service_id else None
    templates = list(db.scalars(select(ResponseTemplate).where(ResponseTemplate.is_active)))

    detail = RequestDetail.model_validate(obj)
    detail.deadlines = report.to_dict()
    detail.legal_entity_name = entity.name if entity else ""
    detail.service_name = service.name if service else ""
    detail.template_matches = [m.to_dict() for m in drafting.rank_templates(obj, templates)][:5]
    return detail


@router.get("/{request_id}", response_model=RequestDetail)
def get_request(request_id: int, db: Session = Depends(get_session)) -> RequestDetail:
    return _detail(db, _get(db, request_id))


@router.post("", response_model=RequestDetail, status_code=201)
def create_request(payload: RequestCreate, db: Session = Depends(get_session)) -> RequestDetail:
    try:
        channel = Channel(payload.channel)
    except ValueError:
        raise HTTPException(422, f"Неизвестный канал: {payload.channel}")
    obj, _ = svc.create_request(
        db,
        inbox_email=payload.inbox_email, requester_email=payload.requester_email,
        requester_name=payload.requester_name, subject_line=payload.subject_line,
        body_text=payload.body_text, received_at=payload.received_at, channel=channel,
        legal_entity_id=payload.legal_entity_id, service_id=payload.service_id,
        analyze=payload.analyze, use_llm=payload.use_llm, actor="user",
    )
    db.commit()
    return _detail(db, _get(db, obj.id))


@router.post("/upload", response_model=RequestDetail, status_code=201)
async def create_from_files(
    files: list[UploadFile] = File(default=[]),
    inbox_email: str = Form(""),
    requester_email: str = Form(""),
    requester_name: str = Form(""),
    subject_line: str = Form(""),
    body_text: str = Form(""),
    received_at: str = Form(""),
    channel: str = Form("EMAIL"),
    use_llm: bool = Form(True),
    db: Session = Depends(get_session),
) -> RequestDetail:
    """Регистрация обращения из файлов: текст, фото, PDF, DOCX, EML."""
    payload: list[tuple[str, bytes]] = []
    limit = settings.max_upload_mb * 1024 * 1024
    for f in files:
        data = await f.read()
        if len(data) > limit:
            raise HTTPException(413, f"Файл «{f.filename}» больше {settings.max_upload_mb} МБ")
        payload.append((f.filename or "файл", data))

    when: datetime | None = None
    if received_at:
        try:
            when = datetime.fromisoformat(received_at)
        except ValueError:
            raise HTTPException(422, "Некорректная дата поступления")

    if not payload and not body_text.strip():
        raise HTTPException(422, "Нужен текст обращения или хотя бы один файл")

    obj, result = svc.create_request(
        db, inbox_email=inbox_email, requester_email=requester_email,
        requester_name=requester_name, subject_line=subject_line, body_text=body_text,
        received_at=when, channel=Channel(channel) if channel in Channel.__members__ else Channel.EMAIL,
        files=payload, use_llm=use_llm, actor="user",
    )

    # Если тело письма пустое, а текст пришёл из вложений — используем его для разбора.
    if not obj.body_text.strip():
        joined = "\n\n".join(a.extracted_text for a in obj.attachments if a.extracted_text)
        if joined:
            obj.body_text = joined
            svc.analyze_request(db, obj, use_llm=use_llm)
    db.commit()
    return _detail(db, _get(db, obj.id))


@router.patch("/{request_id}", response_model=RequestDetail)
def update_request(request_id: int, payload: RequestUpdate,
                   db: Session = Depends(get_session)) -> RequestDetail:
    obj = _get(db, request_id)
    data = payload.model_dump(exclude_unset=True)
    changed: list[str] = []

    if "status" in data and data["status"] is not None:
        try:
            svc.set_status(db, obj, Status(data.pop("status")), actor="user")
        except ValueError:
            raise HTTPException(422, "Неизвестный статус")
        changed.append("status")

    if "identity_confirmed" in data:
        confirmed = data.pop("identity_confirmed")
        obj.identity_confirmed_at = utcnow() if confirmed else None
        changed.append("identity_confirmed")

    if data.pop("clear_manual_due_date", False):
        obj.manual_due_date = None
        changed.append("manual_due_date")

    for field in ("request_type", "subject_type", "requester_kind"):
        if data.get(field) is not None:
            enum_cls = {"request_type": RequestType, "subject_type": SubjectType}.get(field)
            if enum_cls is not None:
                try:
                    enum_cls(data[field])
                except ValueError:
                    raise HTTPException(422, f"Недопустимое значение поля {field}")

    for key, value in data.items():
        if value is not None and hasattr(obj, key):
            setattr(obj, key, value)
            changed.append(key)

    # Ручная правка типа фиксируется, чтобы повторный разбор её не затёр.
    if "request_type" in changed or "subject_type" in changed:
        obj.classified_by = "MANUAL"
        obj.classification_confidence = 1.0
        obj.unconfirmed_fields = []

    if "inbox_email" in changed:
        inbox = svc.resolve_inbox(db, obj.inbox_email)
        obj.inbox_id = inbox.id if inbox else None

    svc.recalculate(db, obj)
    if changed:
        svc.log_event(db, obj, "UPDATED", "Изменены поля: " + ", ".join(changed), actor="user")
    db.commit()
    return _detail(db, _get(db, request_id))


@router.delete("/{request_id}", status_code=204, response_class=Response, response_model=None)
def delete_request(request_id: int, db: Session = Depends(get_session)) -> None:
    obj = _get(db, request_id)
    db.delete(obj)
    db.commit()


@router.post("/{request_id}/reanalyze", response_model=RequestDetail)
def reanalyze(request_id: int, use_llm: bool = True, overwrite_manual: bool = False,
              db: Session = Depends(get_session)) -> RequestDetail:
    obj = _get(db, request_id)
    svc.analyze_request(db, obj, use_llm=use_llm, overwrite_manual=overwrite_manual)
    db.commit()
    return _detail(db, _get(db, request_id))


# --------------------------------------------------------------------------- #
#  Флажки
# --------------------------------------------------------------------------- #

@router.post("/{request_id}/flags", response_model=FlagOut, status_code=201)
def add_flag(request_id: int, payload: FlagIn, db: Session = Depends(get_session)) -> FlagOut:
    obj = _get(db, request_id)
    if payload.level not in (Flag.RED.value, Flag.BLUE.value):
        raise HTTPException(422, "Флажок может быть только RED или BLUE")
    flag = RequestFlag(request_id=obj.id, level=payload.level, code=payload.code,
                       reason=payload.reason, source="MANUAL")
    db.add(flag)
    db.flush()
    svc.recalculate(db, obj)
    svc.log_event(db, obj, "FLAG", f"Поставлен флажок {payload.level}: {payload.reason[:200]}",
                  actor="user")
    db.commit()
    return FlagOut.model_validate(flag)


@router.post("/{request_id}/flags/{flag_id}/resolve", response_model=FlagOut)
def resolve_flag(request_id: int, flag_id: int, payload: FlagResolve,
                 db: Session = Depends(get_session)) -> FlagOut:
    obj = _get(db, request_id)
    flag = db.get(RequestFlag, flag_id)
    if not flag or flag.request_id != obj.id:
        raise HTTPException(404, "Флажок не найден")
    if payload.reopen:
        flag.resolved_at, flag.resolution, flag.resolved_by = None, "", ""
        message = f"Флажок {flag.level} возвращён в работу."
    else:
        flag.resolved_at = utcnow()
        flag.resolution = payload.resolution
        flag.resolved_by = payload.resolved_by
        message = f"Флажок {flag.level} снят: {payload.resolution[:200]}"
    db.flush()
    svc.recalculate(db, obj)
    svc.log_event(db, obj, "FLAG", message, actor=payload.resolved_by or "user")
    db.commit()
    return FlagOut.model_validate(flag)


# --------------------------------------------------------------------------- #
#  Вложения
# --------------------------------------------------------------------------- #

@router.post("/{request_id}/attachments", response_model=list[AttachmentOut], status_code=201)
async def upload_attachments(request_id: int, files: list[UploadFile] = File(...),
                             reanalyze_after: bool = Form(True),
                             db: Session = Depends(get_session)) -> list[AttachmentOut]:
    obj = _get(db, request_id)
    limit = settings.max_upload_mb * 1024 * 1024
    created: list[Attachment] = []
    for f in files:
        data = await f.read()
        if len(data) > limit:
            raise HTTPException(413, f"Файл «{f.filename}» больше {settings.max_upload_mb} МБ")
        att, _ = svc.attach_file(db, obj, f.filename or "файл", data)
        created.append(att)
    if reanalyze_after:
        db.refresh(obj)
        svc.analyze_request(db, obj)
    db.commit()
    return [AttachmentOut.model_validate(a) for a in created]


@router.get("/{request_id}/attachments/{attachment_id}", response_model=AttachmentDetail)
def get_attachment(request_id: int, attachment_id: int,
                   db: Session = Depends(get_session)) -> AttachmentDetail:
    att = db.get(Attachment, attachment_id)
    if not att or att.request_id != request_id:
        raise HTTPException(404, "Вложение не найдено")
    return AttachmentDetail.model_validate(att)


@router.patch("/{request_id}/attachments/{attachment_id}", response_model=AttachmentDetail)
def edit_attachment_text(request_id: int, attachment_id: int, text: str = Form(...),
                         db: Session = Depends(get_session)) -> AttachmentDetail:
    """Ручная правка распознанного текста — если OCR ошибся."""
    obj = _get(db, request_id)
    att = db.get(Attachment, attachment_id)
    if not att or att.request_id != obj.id:
        raise HTTPException(404, "Вложение не найдено")
    att.extracted_text = text
    att.char_count = len(text)
    att.needs_review = False
    att.extraction_method = (att.extraction_method or "") + "+MANUAL"
    db.flush()
    svc.analyze_request(db, obj)
    svc.log_event(db, obj, "ATTACHMENT", f"Текст вложения «{att.filename}» отредактирован вручную.",
                  actor="user")
    db.commit()
    return AttachmentDetail.model_validate(att)


@router.get("/{request_id}/attachments/{attachment_id}/download")
def download_attachment(request_id: int, attachment_id: int,
                        db: Session = Depends(get_session)):
    from pathlib import Path
    att = db.get(Attachment, attachment_id)
    if not att or att.request_id != request_id:
        raise HTTPException(404, "Вложение не найдено")
    path = Path(att.stored_path)
    if not path.exists():
        raise HTTPException(410, "Файл удалён из хранилища")
    return FileResponse(path, filename=att.filename)
