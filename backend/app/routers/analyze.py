"""Разовый разбор без регистрации и приём почты по IMAP."""
from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import classifier as clf
from .. import services as svc
from ..config import settings
from ..db import get_session
from ..deadlines import compute
from ..domain import Channel, RequestType, Status
from ..extraction import extract
from ..models import Inbox, LegalEntity, Service, utcnow
from ..schemas import AnalyzeIn

router = APIRouter(prefix="/api", tags=["analyze"])


def _preview(db: Session, *, body: str, subject_line: str, from_email: str,
             inbox_email: str, use_llm: bool, files_info: list[dict]) -> dict:
    """Общая часть: классификация + расчёт срока без сохранения в реестр."""
    from .. import llm as llm_mod

    inbox = svc.resolve_inbox(db, inbox_email)
    cls = clf.classify(
        body=body, subject_line=subject_line, from_email=from_email,
        inbox_purpose=(inbox.purpose if inbox else ""),
    )
    llm_info = {"used": False}
    if use_llm and llm_mod.available():
        res = llm_mod.analyze(subject_line=subject_line, body=body,
                              from_email=from_email, inbox_email=inbox_email)
        llm_info = {"used": res.ok, "error": res.error, "model": res.model}
        if res.ok:
            cls = svc.merge_llm(cls, res.data)

    entities = list(db.scalars(select(LegalEntity).where(LegalEntity.is_active)))
    services = list(db.scalars(select(Service).where(Service.is_active)))
    full = f"{subject_line}\n{body}"
    ent_id, ent_name = clf.match_legal_entity(full, entities)
    svc_id, svc_name = clf.match_service(full, services, cls.subject_type)

    report = compute(cls.request_type, utcnow(), status=Status.NEW)

    return {
        "classification": cls.to_dict(),
        "deadlines": report.to_dict(),
        "llm": llm_info,
        "legal_entity": {"id": ent_id, "name": ent_name or cls.legal_entity_mentioned},
        "service": {"id": svc_id, "name": svc_name or cls.service_mentioned},
        "inbox": {"email": inbox.email if inbox else inbox_email,
                  "known": bool(inbox),
                  "purpose": inbox.purpose if inbox else ""},
        "files": files_info,
        "text_length": len(body),
    }


@router.post("/analyze")
def analyze_text(payload: AnalyzeIn, db: Session = Depends(get_session)) -> dict:
    """Разобрать произвольный текст, ничего не создавая в реестре."""
    if not payload.text.strip():
        raise HTTPException(422, "Пустой текст")
    return _preview(db, body=payload.text, subject_line=payload.subject_line,
                    from_email=payload.from_email, inbox_email=payload.inbox_email,
                    use_llm=payload.use_llm, files_info=[])


@router.post("/analyze/files")
async def analyze_files(
    files: list[UploadFile] = File(...),
    subject_line: str = Form(""),
    from_email: str = Form(""),
    inbox_email: str = Form(""),
    body_text: str = Form(""),
    use_llm: bool = Form(True),
    db: Session = Depends(get_session),
) -> dict:
    """
    Загрузить текст, фото или PDF, получить извлечённый текст и разбор.

    Ничего не сохраняется в реестр — это режим «посмотреть, что это такое».
    """
    limit = settings.max_upload_mb * 1024 * 1024
    chunks: list[str] = [body_text] if body_text.strip() else []
    info: list[dict] = []
    for f in files:
        data = await f.read()
        if len(data) > limit:
            raise HTTPException(413, f"Файл «{f.filename}» больше {settings.max_upload_mb} МБ")
        res = extract(f.filename or "файл", data)
        info.append({
            "filename": f.filename, "method": res.method, "chars": res.char_count,
            "pages": res.page_count, "error": res.error, "warnings": res.warnings,
            "needs_review": res.needs_review, "text": res.text,
        })
        if res.text:
            chunks.append(res.text)

    combined = "\n\n".join(chunks)
    if not combined.strip():
        errors = "; ".join(f"{i['filename']}: {i['error']}" for i in info if i["error"])
        raise HTTPException(
            422, f"Не удалось извлечь текст ни из одного файла. {errors}"
        )
    result = _preview(db, body=combined, subject_line=subject_line, from_email=from_email,
                      inbox_email=inbox_email, use_llm=use_llm, files_info=info)
    result["extracted_text"] = combined
    return result


# --------------------------------------------------------------------------- #
#  IMAP
# --------------------------------------------------------------------------- #

@router.post("/inboxes/{inbox_id}/sync")
def sync_inbox(inbox_id: int, limit: int = 50, use_llm: bool = True,
               db: Session = Depends(get_session)) -> dict:
    """
    Забрать непрочитанные письма из ящика и зарегистрировать их как обращения.

    Пароль берётся из переменной окружения, имя которой указано в настройках
    ящика, — секреты в базе не хранятся.
    """
    inbox = db.get(Inbox, inbox_id)
    if not inbox:
        raise HTTPException(404, "Ящик не найден")
    if not inbox.imap_host:
        raise HTTPException(422, "Для этого ящика не заданы настройки IMAP")

    password = os.environ.get(inbox.imap_password_env or "", "")
    if not password:
        raise HTTPException(
            422,
            f"Пароль не найден. Задайте переменную окружения "
            f"«{inbox.imap_password_env or 'не указана'}» и перезапустите сервис.",
        )

    try:
        from imap_tools import AND, MailBox
    except ImportError:
        raise HTTPException(500, "Библиотека imap-tools не установлена")

    created, skipped, errors = [], 0, []
    try:
        with MailBox(inbox.imap_host, port=inbox.imap_port).login(
            inbox.imap_user or inbox.email, password, initial_folder=inbox.imap_folder
        ) as mailbox:
            for msg in mailbox.fetch(AND(seen=False), limit=limit, mark_seen=False,
                                     bulk=True, reverse=True):
                exists = db.scalar(select(svc.Request).where(
                    svc.Request.message_id == (msg.uid or msg.subject)))
                if exists:
                    skipped += 1
                    continue
                files = [(a.filename or "вложение", a.payload) for a in msg.attachments]
                try:
                    obj, _ = svc.create_request(
                        db,
                        inbox_email=inbox.email,
                        requester_email=msg.from_ or "",
                        requester_name=(msg.from_values.name if msg.from_values else ""),
                        subject_line=msg.subject or "",
                        body_text=msg.text or msg.html or "",
                        received_at=msg.date.replace(tzinfo=None) if msg.date else None,
                        channel=Channel.EMAIL,
                        message_id=msg.uid or "",
                        files=files,
                        use_llm=use_llm,
                        actor="imap",
                    )
                    created.append(obj.reg_number)
                except Exception as exc:
                    errors.append(f"{msg.subject}: {exc}")
            inbox.imap_last_sync_at = utcnow()
        db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Не удалось подключиться к почтовому ящику: {exc}")

    return {"created": created, "created_count": len(created),
            "skipped": skipped, "errors": errors}


@router.post("/maintenance/recalculate")
def recalculate(db: Session = Depends(get_session)) -> dict:
    """
    Пересчитать срочность по всем обращениям.

    Нужен раз в сутки: «осталось 3 рабочих дня» меняется от того, что наступил
    новый день, а не от того, что кто-то открыл карточку.
    """
    count = svc.recalculate_all(db)
    db.commit()
    return {"recalculated": count}
