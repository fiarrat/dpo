"""Справочники, словари перечислений и состояние системы."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import calendar_ru, extraction, llm
from ..db import get_session
from ..domain import (
    REQUEST_TYPE_LABELS, RKN_TYPES, NON_PD_TYPES, STATUS_LABELS, SUBJECT_TYPES_OF_REQUEST,
    SUBJECT_TYPE_LABELS, SLA_RULES, URGENCY_LABELS, Channel, RequesterKind, RequestType,
    Status, SubjectType, Urgency,
)
from ..models import Inbox, LegalEntity, Service
from ..schemas import (
    InboxIn, InboxOut, LegalEntityIn, LegalEntityOut, ServiceIn, ServiceOut,
)

router = APIRouter(prefix="/api", tags=["reference"])

REQUESTER_KIND_LABELS = {
    RequesterKind.SUBJECT: "Субъект персональных данных",
    RequesterKind.SUBJECT_REPRESENTATIVE: "Представитель субъекта",
    RequesterKind.RKN: "Роскомнадзор",
    RequesterKind.OTHER_AUTHORITY: "Иной государственный орган",
    RequesterKind.COMPANY: "Организация",
    RequesterKind.UNKNOWN: "Не определён",
}

CHANNEL_LABELS = {
    Channel.EMAIL: "Электронная почта",
    Channel.PAPER: "Бумажное письмо",
    Channel.PORTAL: "Форма на сайте",
    Channel.MANUAL: "Внесено вручную",
}

SERVICE_CATEGORIES = {
    "PRODUCT": "Продукт / сервис", "HR": "Кадры", "MARKETING": "Маркетинг",
    "SALES": "Продажи", "SUPPORT": "Поддержка", "FINANCE": "Финансы",
    "SECURITY": "Безопасность", "OTHER": "Прочее",
}


def _opts(labels: dict) -> list[dict]:
    return [{"value": k.value if hasattr(k, "value") else k, "label": v}
            for k, v in labels.items()]


@router.get("/reference")
def reference() -> dict:
    """Всё, что нужно интерфейсу для отрисовки фильтров и форм."""
    return {
        "request_types": [
            {
                "value": t.value,
                "label": REQUEST_TYPE_LABELS[t],
                "group": ("RKN" if t in RKN_TYPES
                          else "SUBJECT" if t in SUBJECT_TYPES_OF_REQUEST
                          else "NON_PD" if t in NON_PD_TYPES else "OTHER"),
                "sla": SLA_RULES[t].summary if t in SLA_RULES else "",
                "deadlines": [
                    {"code": d.code, "title": d.title, "amount": d.amount,
                     "unit": d.unit.value, "legal_ref": d.legal_ref, "note": d.note,
                     "extension_days": d.extension_days,
                     "extension_ref": d.extension_ref}
                    for d in (SLA_RULES[t].deadlines if t in SLA_RULES else ())
                ],
            }
            for t in RequestType
        ],
        "type_groups": {
            "SUBJECT": "Обращения субъектов ПД",
            "RKN": "Роскомнадзор и госорганы",
            "NON_PD": "Не относится к персональным данным",
            "OTHER": "Прочее",
        },
        "subject_types": _opts(SUBJECT_TYPE_LABELS),
        "requester_kinds": _opts(REQUESTER_KIND_LABELS),
        "statuses": _opts(STATUS_LABELS),
        "urgencies": _opts(URGENCY_LABELS),
        "channels": _opts(CHANNEL_LABELS),
        "service_categories": [{"value": k, "label": v} for k, v in SERVICE_CATEGORIES.items()],
        "system": {
            "calendar": calendar_ru.calendar_status(),
            "extraction": extraction.tool_status(),
            "llm": llm.status(),
        },
    }


# --------------------------------------------------------------------------- #
#  CRUD справочников
# --------------------------------------------------------------------------- #

def _crud(prefix: str, model, schema_in, schema_out, tag: str) -> APIRouter:
    """
    Однотипный CRUD для справочников.

    Аннотации проставляются после определения функций: в модуле включён
    `from __future__ import annotations`, поэтому FastAPI разбирает подписи через
    get_type_hints по глобальным именам модуля и не видит локальную переменную
    schema_in — без явной подстановки тело запроса уезжало бы в query-параметры.
    """
    r = APIRouter(prefix=f"/api/{prefix}", tags=[tag])

    def list_items(include_inactive: bool = True, db: Session = Depends(get_session)):
        stmt = select(model).order_by(model.id)
        if not include_inactive:
            stmt = stmt.where(model.is_active)
        return list(db.scalars(stmt))

    def create(payload, db: Session = Depends(get_session)):
        obj = model(**payload.model_dump())
        db.add(obj)
        db.flush()
        return obj

    def update(item_id: int, payload, db: Session = Depends(get_session)):
        obj = db.get(model, item_id)
        if not obj:
            raise HTTPException(404, "Запись не найдена")
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(obj, k, v)
        db.flush()
        return obj

    def delete(item_id: int, db: Session = Depends(get_session)):
        obj = db.get(model, item_id)
        if not obj:
            raise HTTPException(404, "Запись не найдена")
        # Мягкое удаление: на записи могут ссылаться уже принятые обращения.
        obj.is_active = False
        db.flush()

    create.__annotations__["payload"] = schema_in
    update.__annotations__["payload"] = schema_in

    r.get("", response_model=list[schema_out])(list_items)
    r.post("", response_model=schema_out, status_code=201)(create)
    r.patch("/{item_id}", response_model=schema_out)(update)
    r.delete("/{item_id}", status_code=204, response_class=Response,
             response_model=None)(delete)
    return r


legal_entities_router = _crud("legal-entities", LegalEntity, LegalEntityIn,
                              LegalEntityOut, "legal-entities")
inboxes_router = _crud("inboxes", Inbox, InboxIn, InboxOut, "inboxes")
services_router = _crud("services", Service, ServiceIn, ServiceOut, "services")
