"""Схема данных реестра обращений."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .domain import Channel, Flag, RequesterKind, RequestType, Status, SubjectType


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- #
#  Справочники
# --------------------------------------------------------------------------- #

class LegalEntity(Base, TimestampMixin):
    """Юридическое лицо — оператор ПД, указанное в обращении."""
    __tablename__ = "legal_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    short_name: Mapped[str] = mapped_column(String(120), default="")
    inn: Mapped[str] = mapped_column(String(12), default="", index=True)
    kpp: Mapped[str] = mapped_column(String(9), default="")
    ogrn: Mapped[str] = mapped_column(String(15), default="")
    address: Mapped[str] = mapped_column(String(500), default="")
    #: Регистрационный номер в реестре операторов ПД (для ответов и уведомлений).
    rkn_operator_number: Mapped[str] = mapped_column(String(50), default="")
    dpo_name: Mapped[str] = mapped_column(String(200), default="")
    dpo_email: Mapped[str] = mapped_column(String(200), default="")
    #: Ключевые слова для автоопределения ЮЛ в тексте обращения.
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    inboxes: Mapped[list["Inbox"]] = relationship(back_populates="legal_entity")
    services: Mapped[list["Service"]] = relationship(back_populates="legal_entity")


class Inbox(Base, TimestampMixin):
    """Почтовый ящик, на который приходят обращения."""
    __tablename__ = "inboxes"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(150), default="")
    #: ЮЛ по умолчанию для обращений, пришедших на этот ящик.
    legal_entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("legal_entities.id", ondelete="SET NULL"), nullable=True
    )
    #: Тематика ящика — подсказка классификатору (privacy@, dpo@, hr@, support@).
    purpose: Mapped[str] = mapped_column(String(60), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Настройки IMAP (пароль хранится вне БД — в переменной окружения).
    imap_host: Mapped[str] = mapped_column(String(200), default="")
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    imap_user: Mapped[str] = mapped_column(String(200), default="")
    imap_password_env: Mapped[str] = mapped_column(String(100), default="")
    imap_folder: Mapped[str] = mapped_column(String(100), default="INBOX")
    imap_last_uid: Mapped[str] = mapped_column(String(50), default="")
    imap_last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    legal_entity: Mapped[Optional[LegalEntity]] = relationship(back_populates="inboxes")


class Service(Base, TimestampMixin):
    """Сервис или бизнес-процесс, в рамках которого обрабатываются ПД."""
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(60), default="", index=True)
    #: PRODUCT | HR | MARKETING | SALES | SUPPORT | FINANCE | SECURITY | OTHER
    category: Mapped[str] = mapped_column(String(40), default="OTHER")
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(200), default="")
    owner_email: Mapped[str] = mapped_column(String(200), default="")
    legal_entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("legal_entities.id", ondelete="SET NULL"), nullable=True
    )
    #: ИСПДн / системы, где лежат ПД — попадают в ответ по ч. 7 ст. 14.
    systems: Mapped[list] = mapped_column(JSON, default=list)
    #: Ключевые слова для автоопределения сервиса в тексте обращения.
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    #: Типовые категории субъектов для этого сервиса.
    subject_types: Mapped[list] = mapped_column(JSON, default=list)
    retention_note: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    legal_entity: Mapped[Optional[LegalEntity]] = relationship(back_populates="services")

    __table_args__ = (UniqueConstraint("code", name="uq_service_code"),)


# --------------------------------------------------------------------------- #
#  Обращения
# --------------------------------------------------------------------------- #

class Request(Base, TimestampMixin):
    """Обращение / запрос субъекта ПД или Роскомнадзора."""
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Регистрационный номер вида ПД-2026-000123.
    reg_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)

    # --- поступление --------------------------------------------------------
    channel: Mapped[str] = mapped_column(String(20), default=Channel.EMAIL.value)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    #: Ящик, на который пришло обращение (обязательное требование заказчика).
    inbox_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("inboxes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    inbox_email: Mapped[str] = mapped_column(String(200), default="", index=True)
    message_id: Mapped[str] = mapped_column(String(300), default="", index=True)

    # --- заявитель ----------------------------------------------------------
    requester_name: Mapped[str] = mapped_column(String(300), default="")
    requester_email: Mapped[str] = mapped_column(String(200), default="", index=True)
    requester_phone: Mapped[str] = mapped_column(String(50), default="")
    requester_kind: Mapped[str] = mapped_column(
        String(30), default=RequesterKind.UNKNOWN.value, index=True
    )
    subject_type: Mapped[str] = mapped_column(
        String(30), default=SubjectType.UNKNOWN.value, index=True
    )
    #: ФИО субъекта, если обращается представитель.
    subject_name: Mapped[str] = mapped_column(String(300), default="")

    # --- содержание ---------------------------------------------------------
    subject_line: Mapped[str] = mapped_column(String(500), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    request_type: Mapped[str] = mapped_column(
        String(40), default=RequestType.UNCLASSIFIED.value, index=True
    )
    #: Дополнительные требования, если в одном письме их несколько.
    secondary_types: Mapped[list] = mapped_column(JSON, default=list)

    # --- привязка -----------------------------------------------------------
    legal_entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("legal_entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Название ЮЛ как оно указано в самом обращении (может не совпадать со справочником).
    legal_entity_mentioned: Mapped[str] = mapped_column(String(300), default="")
    service_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL"), nullable=True, index=True
    )
    service_mentioned: Mapped[str] = mapped_column(String(300), default="")

    # --- обработка ----------------------------------------------------------
    status: Mapped[str] = mapped_column(String(30), default=Status.NEW.value, index=True)
    assignee: Mapped[str] = mapped_column(String(200), default="")
    #: Личность / полномочия подтверждены (ч. 4 ст. 14) — от этой даты идёт срок.
    identity_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    identity_note: Mapped[str] = mapped_column(Text, default="")
    #: Срок, указанный в самом документе (предписание РКН, запрос госоргана).
    manual_due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    extension_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    extension_reason: Mapped[str] = mapped_column(Text, default="")
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, default="")

    # --- денормализация для быстрых фильтров и сортировки -------------------
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    urgency: Mapped[str] = mapped_column(String(20), default="NONE", index=True)
    has_red_flag: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    has_blue_flag: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # --- разбор -------------------------------------------------------------
    #: Как определён тип: RULES | LLM | MANUAL.
    classified_by: Mapped[str] = mapped_column(String(20), default="")
    classification_confidence: Mapped[float] = mapped_column(default=0.0)
    classification: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    #: Поля, заполненные автоматически и не подтверждённые человеком.
    unconfirmed_fields: Mapped[list] = mapped_column(JSON, default=list)

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )
    flags: Mapped[list["RequestFlag"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )
    drafts: Mapped[list["Draft"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="request", cascade="all, delete-orphan",
        order_by="Event.created_at.desc()",
    )
    legal_entity: Mapped[Optional[LegalEntity]] = relationship()
    service: Mapped[Optional[Service]] = relationship()
    inbox: Mapped[Optional[Inbox]] = relationship()

    __table_args__ = (
        Index("ix_requests_board", "status", "urgency", "due_date"),
    )


class RequestFlag(Base, TimestampMixin):
    """
    Флажок на обращении.

    RED  — обращение очевидно не связано с персональными данными.
    BLUE — спорный момент, требующий решения DPO.
    """
    __tablename__ = "request_flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(60), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    #: RULES | LLM | MANUAL
    source: Mapped[str] = mapped_column(String(20), default="RULES")
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str] = mapped_column(String(200), default="")
    resolution: Mapped[str] = mapped_column(Text, default="")

    request: Mapped[Request] = relationship(back_populates="flags")


class Attachment(Base, TimestampMixin):
    """Вложение: текст, изображение, PDF, docx. Всё приводится к тексту."""
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(400), nullable=False)
    content_type: Mapped[str] = mapped_column(String(150), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    stored_path: Mapped[str] = mapped_column(String(700), default="")

    extracted_text: Mapped[str] = mapped_column(Text, default="")
    #: PLAIN | PDF_TEXT | PDF_OCR | OCR | DOCX | XLSX | EML | HTML | LLM_VISION | FAILED
    extraction_method: Mapped[str] = mapped_column(String(30), default="")
    extraction_error: Mapped[str] = mapped_column(Text, default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Требуется ручная вычитка (OCR низкого качества / текст не извлёкся).
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    request: Mapped[Optional[Request]] = relationship(back_populates="attachments")


class ResponseTemplate(Base, TimestampMixin):
    """Типовой ответ, загружаемый пользователем."""
    __tablename__ = "response_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    #: Для каких типов обращений применим (пусто — универсальный).
    request_types: Mapped[list] = mapped_column(JSON, default=list)
    #: Для каких видов субъектов применим (пусто — любые).
    subject_types: Mapped[list] = mapped_column(JSON, default=list)
    requester_kinds: Mapped[list] = mapped_column(JSON, default=list)
    legal_entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("legal_entities.id", ondelete="SET NULL"), nullable=True
    )
    subject_line: Mapped[str] = mapped_column(String(400), default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Найденные в теле плейсхолдеры {{...}}.
    placeholders: Mapped[list] = mapped_column(JSON, default=list)
    source_filename: Mapped[str] = mapped_column(String(400), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)


class Draft(Base, TimestampMixin):
    """Драфт ответа на обращение."""
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), index=True
    )
    template_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("response_templates.id", ondelete="SET NULL"), nullable=True
    )
    subject_line: Mapped[str] = mapped_column(String(400), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    #: TEMPLATE | TEMPLATE+LLM | LLM
    generator: Mapped[str] = mapped_column(String(30), default="TEMPLATE")
    #: Плейсхолдеры, которые не удалось заполнить, — их надо дописать руками.
    unresolved_placeholders: Mapped[list] = mapped_column(JSON, default=list)
    checklist: Mapped[list] = mapped_column(JSON, default=list)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    author: Mapped[str] = mapped_column(String(200), default="")

    request: Mapped[Request] = relationship(back_populates="drafts")
    template: Mapped[Optional[ResponseTemplate]] = relationship()


class Event(Base):
    """Журнал действий по обращению — нужен для доказывания соблюдения сроков."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(200), default="system")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    request: Mapped[Request] = relationship(back_populates="events")
