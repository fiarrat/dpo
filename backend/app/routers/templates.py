"""Типовые ответы и генерация драфтов."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import drafting, llm
from .. import services as svc
from ..config import settings
from ..db import get_session
from ..domain import RequesterKind, RequestType, Status, SubjectType
from ..extraction import extract
from ..models import Draft, LegalEntity, Request, ResponseTemplate, Service
from ..schemas import (
    DraftCreate, DraftOut, DraftUpdate, TemplateIn, TemplateOut, TemplateUpdate,
)

router = APIRouter(prefix="/api/templates", tags=["templates"])


def _validate_lists(payload) -> None:
    for value in (payload.request_types or []):
        try:
            RequestType(value)
        except ValueError:
            raise HTTPException(422, f"Неизвестный тип обращения: {value}")
    for value in (payload.subject_types or []):
        try:
            SubjectType(value)
        except ValueError:
            raise HTTPException(422, f"Неизвестный вид субъекта: {value}")
    for value in (payload.requester_kinds or []):
        try:
            RequesterKind(value)
        except ValueError:
            raise HTTPException(422, f"Неизвестный тип заявителя: {value}")


@router.get("", response_model=list[TemplateOut])
def list_templates(include_inactive: bool = True, db: Session = Depends(get_session)):
    stmt = select(ResponseTemplate).order_by(ResponseTemplate.id.desc())
    if not include_inactive:
        stmt = stmt.where(ResponseTemplate.is_active)
    return list(db.scalars(stmt))


@router.post("", response_model=TemplateOut, status_code=201)
def create_template(payload: TemplateIn, db: Session = Depends(get_session)):
    _validate_lists(payload)
    obj = ResponseTemplate(**payload.model_dump())
    obj.placeholders = drafting.find_placeholders(obj.body)
    db.add(obj)
    db.commit()
    return obj


@router.post("/upload", response_model=list[TemplateOut], status_code=201)
async def upload_templates(
    files: list[UploadFile] = File(...),
    request_types: str = Form(""),
    subject_types: str = Form(""),
    requester_kinds: str = Form(""),
    legal_entity_id: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_session),
):
    """
    Загрузка типовых ответов файлами (docx / pdf / txt / md).

    Каждый файл становится отдельным шаблоном; плейсхолдеры извлекаются
    автоматически и показываются в интерфейсе.
    """
    def split(value: str) -> list[str]:
        return [v.strip() for v in (value or "").split(",") if v.strip()]

    types, subs, kinds = split(request_types), split(subject_types), split(requester_kinds)
    for value in types:
        try:
            RequestType(value)
        except ValueError:
            raise HTTPException(422, f"Неизвестный тип обращения: {value}")

    limit = settings.max_upload_mb * 1024 * 1024
    created: list[ResponseTemplate] = []
    problems: list[str] = []
    for f in files:
        data = await f.read()
        if len(data) > limit:
            raise HTTPException(413, f"Файл «{f.filename}» больше {settings.max_upload_mb} МБ")
        res = extract(f.filename or "шаблон", data)
        if not res.text.strip():
            problems.append(f"{f.filename}: {res.error or 'не удалось извлечь текст'}")
            continue
        obj = ResponseTemplate(
            title=(f.filename or "Типовой ответ").rsplit(".", 1)[0],
            body=res.text,
            request_types=types, subject_types=subs, requester_kinds=kinds,
            legal_entity_id=int(legal_entity_id) if legal_entity_id.isdigit() else None,
            notes=notes, source_filename=f.filename or "",
            placeholders=drafting.find_placeholders(res.text),
        )
        db.add(obj)
        created.append(obj)
    if not created:
        raise HTTPException(422, "Ни один файл не удалось прочитать. " + "; ".join(problems))
    db.commit()
    return created


@router.patch("/{template_id}", response_model=TemplateOut)
def update_template(template_id: int, payload: TemplateUpdate,
                    db: Session = Depends(get_session)):
    obj = db.get(ResponseTemplate, template_id)
    if not obj:
        raise HTTPException(404, "Шаблон не найден")
    _validate_lists(payload)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(obj, key, value)
    obj.placeholders = drafting.find_placeholders(obj.body)
    db.commit()
    return obj


@router.delete("/{template_id}", status_code=204, response_class=Response, response_model=None)
def delete_template(template_id: int, db: Session = Depends(get_session)) -> None:
    obj = db.get(ResponseTemplate, template_id)
    if not obj:
        raise HTTPException(404, "Шаблон не найден")
    db.delete(obj)
    db.commit()


# --------------------------------------------------------------------------- #
#  Драфты
# --------------------------------------------------------------------------- #

drafts_router = APIRouter(prefix="/api", tags=["drafts"])


@drafts_router.post("/requests/{request_id}/draft", response_model=DraftOut, status_code=201)
def generate_draft(request_id: int, payload: DraftCreate,
                   db: Session = Depends(get_session)) -> DraftOut:
    """
    Собрать драфт ответа.

    Порядок выбора основы:
    1. Явно указанный шаблон.
    2. Лучший подходящий из загруженных типовых ответов.
    3. Структурная заготовка по типу обращения, если типовых ответов нет.

    ИИ, если он подключён, дорабатывает текст под конкретное обращение,
    но сроки в текст подставляются уже рассчитанные.
    """
    obj = db.scalar(
        select(Request).where(Request.id == request_id)
        .options(selectinload(Request.flags), selectinload(Request.attachments))
    )
    if not obj:
        raise HTTPException(404, "Обращение не найдено")

    report = svc.deadline_report(obj)
    entity = db.get(LegalEntity, obj.legal_entity_id) if obj.legal_entity_id else None
    service = db.get(Service, obj.service_id) if obj.service_id else None
    ctx = drafting.build_context(request=obj, deadlines=report,
                                 legal_entity=entity, service=service)

    template: ResponseTemplate | None = None
    if payload.template_id:
        template = db.get(ResponseTemplate, payload.template_id)
        if not template:
            raise HTTPException(404, "Шаблон не найден")
    else:
        candidates = list(db.scalars(
            select(ResponseTemplate).where(ResponseTemplate.is_active)))
        ranked = drafting.rank_templates(obj, candidates)
        if ranked:
            template = db.get(ResponseTemplate, ranked[0].template_id)

    unresolved: list[str] = []
    if template:
        body, unresolved = drafting.fill(template.body, ctx)
        generator = "TEMPLATE"
        subject_line = template.subject_line or f"Ответ на обращение {obj.reg_number}"
    else:
        body = drafting.fallback_draft(obj, ctx)
        generator = "FALLBACK"
        subject_line = f"Ответ на обращение {obj.reg_number}"

    llm_error = ""
    if payload.use_llm and llm.available():
        context = drafting.build_llm_context(obj, report, obj.flags, ctx)
        if payload.instructions:
            context += f"\n\n=== ДОПОЛНИТЕЛЬНЫЕ УКАЗАНИЯ ===\n{payload.instructions}"
        res = llm.draft_response(context=context,
                                 template_body=template.body if template else "")
        if res.ok and res.data.get("body"):
            filled, unresolved = drafting.fill(res.data["body"], ctx)
            body = filled
            generator = "TEMPLATE+LLM" if template else "LLM"
        else:
            llm_error = res.error

    draft = Draft(
        request_id=obj.id,
        template_id=template.id if template else None,
        subject_line=subject_line,
        body=body,
        generator=generator,
        unresolved_placeholders=unresolved,
        checklist=drafting.build_checklist(obj, report, obj.flags),
        author="system",
    )
    db.add(draft)
    if template:
        template.usage_count += 1
    if obj.status in (Status.NEW.value, Status.TRIAGE.value, Status.IN_PROGRESS.value):
        obj.status = Status.DRAFTED.value
    svc.log_event(db, obj, "DRAFT",
                  f"Сформирован драфт ({generator})"
                  + (f", шаблон «{template.title}»" if template else "")
                  + (f". ИИ недоступен: {llm_error}" if llm_error else "."),
                  actor="user")
    svc.recalculate(db, obj)
    db.commit()
    return DraftOut.model_validate(draft)


@drafts_router.get("/drafts/{draft_id}", response_model=DraftOut)
def get_draft(draft_id: int, db: Session = Depends(get_session)) -> DraftOut:
    obj = db.get(Draft, draft_id)
    if not obj:
        raise HTTPException(404, "Драфт не найден")
    return DraftOut.model_validate(obj)


@drafts_router.patch("/drafts/{draft_id}", response_model=DraftOut)
def update_draft(draft_id: int, payload: DraftUpdate,
                 db: Session = Depends(get_session)) -> DraftOut:
    obj = db.get(Draft, draft_id)
    if not obj:
        raise HTTPException(404, "Драфт не найден")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(obj, key, value)
    if payload.body is not None:
        obj.unresolved_placeholders = drafting.find_placeholders(obj.body)
    db.commit()
    return DraftOut.model_validate(obj)


@drafts_router.delete("/drafts/{draft_id}", status_code=204, response_class=Response, response_model=None)
def delete_draft(draft_id: int, db: Session = Depends(get_session)) -> None:
    obj = db.get(Draft, draft_id)
    if not obj:
        raise HTTPException(404, "Драфт не найден")
    db.delete(obj)
    db.commit()
