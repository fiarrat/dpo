"""Pydantic-схемы API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
#  Справочники
# --------------------------------------------------------------------------- #

class LegalEntityIn(BaseModel):
    name: str
    short_name: str = ""
    inn: str = ""
    kpp: str = ""
    ogrn: str = ""
    address: str = ""
    rkn_operator_number: str = ""
    dpo_name: str = ""
    dpo_email: str = ""
    aliases: list[str] = Field(default_factory=list)
    is_active: bool = True


class LegalEntityOut(LegalEntityIn, ORMModel):
    id: int


class InboxIn(BaseModel):
    email: str
    label: str = ""
    legal_entity_id: Optional[int] = None
    purpose: str = ""
    is_active: bool = True
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password_env: str = ""
    imap_folder: str = "INBOX"


class InboxOut(InboxIn, ORMModel):
    id: int
    imap_last_sync_at: Optional[datetime] = None


class ServiceIn(BaseModel):
    name: str
    code: str = ""
    category: str = "OTHER"
    description: str = ""
    owner: str = ""
    owner_email: str = ""
    legal_entity_id: Optional[int] = None
    systems: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    subject_types: list[str] = Field(default_factory=list)
    retention_note: str = ""
    is_active: bool = True


class ServiceOut(ServiceIn, ORMModel):
    id: int


# --------------------------------------------------------------------------- #
#  Обращения
# --------------------------------------------------------------------------- #

class RequestCreate(BaseModel):
    inbox_email: str = ""
    requester_email: str = ""
    requester_name: str = ""
    subject_line: str = ""
    body_text: str = ""
    received_at: Optional[datetime] = None
    channel: str = "EMAIL"
    legal_entity_id: Optional[int] = None
    service_id: Optional[int] = None
    analyze: bool = True
    use_llm: bool = True


class RequestUpdate(BaseModel):
    """Все поля опциональны — PATCH меняет только присланное."""
    requester_name: Optional[str] = None
    requester_email: Optional[str] = None
    requester_phone: Optional[str] = None
    requester_kind: Optional[str] = None
    subject_type: Optional[str] = None
    subject_name: Optional[str] = None
    subject_line: Optional[str] = None
    body_text: Optional[str] = None
    request_type: Optional[str] = None
    secondary_types: Optional[list[str]] = None
    legal_entity_id: Optional[int] = None
    legal_entity_mentioned: Optional[str] = None
    service_id: Optional[int] = None
    service_mentioned: Optional[str] = None
    inbox_email: Optional[str] = None
    status: Optional[str] = None
    assignee: Optional[str] = None
    identity_confirmed: Optional[bool] = None
    identity_note: Optional[str] = None
    manual_due_date: Optional[date] = None
    clear_manual_due_date: bool = False
    extension_applied: Optional[bool] = None
    extension_reason: Optional[str] = None
    received_at: Optional[datetime] = None
    outcome: Optional[str] = None


class FlagOut(ORMModel):
    id: int
    level: str
    code: str
    reason: str
    source: str
    resolved_at: Optional[datetime] = None
    resolved_by: str = ""
    resolution: str = ""
    created_at: datetime


class FlagIn(BaseModel):
    level: str
    code: str = "MANUAL"
    reason: str


class FlagResolve(BaseModel):
    resolution: str = ""
    resolved_by: str = "user"
    reopen: bool = False


class AttachmentOut(ORMModel):
    id: int
    filename: str
    content_type: str
    size_bytes: int
    extraction_method: str
    extraction_error: str
    page_count: int
    char_count: int
    needs_review: bool
    created_at: datetime


class AttachmentDetail(AttachmentOut):
    extracted_text: str = ""


class EventOut(ORMModel):
    id: int
    created_at: datetime
    kind: str
    message: str
    actor: str
    payload: dict = Field(default_factory=dict)


class DraftOut(ORMModel):
    id: int
    request_id: int
    template_id: Optional[int]
    subject_line: str
    body: str
    generator: str
    unresolved_placeholders: list[str] = Field(default_factory=list)
    checklist: list[dict] = Field(default_factory=list)
    is_final: bool
    author: str
    created_at: datetime
    updated_at: datetime


class DraftCreate(BaseModel):
    template_id: Optional[int] = None
    use_llm: bool = True
    instructions: str = ""


class DraftUpdate(BaseModel):
    subject_line: Optional[str] = None
    body: Optional[str] = None
    is_final: Optional[bool] = None
    checklist: Optional[list[dict]] = None


class RequestListItem(ORMModel):
    id: int
    reg_number: str
    received_at: datetime
    inbox_email: str
    requester_name: str
    requester_email: str
    requester_kind: str
    subject_type: str
    subject_line: str
    request_type: str
    secondary_types: list[str] = Field(default_factory=list)
    legal_entity_id: Optional[int]
    legal_entity_mentioned: str
    service_id: Optional[int]
    service_mentioned: str
    status: str
    assignee: str
    due_date: Optional[date]
    urgency: str
    has_red_flag: bool
    has_blue_flag: bool
    classification_confidence: float
    classified_by: str
    summary: str
    manual_due_date: Optional[date]
    extension_applied: bool
    identity_confirmed_at: Optional[datetime]
    unconfirmed_fields: list[str] = Field(default_factory=list)


class RequestDetail(RequestListItem):
    body_text: str = ""
    subject_name: str = ""
    requester_phone: str = ""
    identity_note: str = ""
    extension_reason: str = ""
    outcome: str = ""
    answered_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: datetime
    classification: dict = Field(default_factory=dict)
    flags: list[FlagOut] = Field(default_factory=list)
    attachments: list[AttachmentOut] = Field(default_factory=list)
    drafts: list[DraftOut] = Field(default_factory=list)
    events: list[EventOut] = Field(default_factory=list)
    deadlines: dict = Field(default_factory=dict)
    legal_entity_name: str = ""
    service_name: str = ""
    template_matches: list[dict] = Field(default_factory=list)


class RequestPage(BaseModel):
    items: list[RequestListItem]
    total: int
    page: int
    page_size: int
    facets: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
#  Типовые ответы
# --------------------------------------------------------------------------- #

class TemplateIn(BaseModel):
    title: str
    body: str
    request_types: list[str] = Field(default_factory=list)
    subject_types: list[str] = Field(default_factory=list)
    requester_kinds: list[str] = Field(default_factory=list)
    legal_entity_id: Optional[int] = None
    subject_line: str = ""
    notes: str = ""
    is_active: bool = True


class TemplateUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    request_types: Optional[list[str]] = None
    subject_types: Optional[list[str]] = None
    requester_kinds: Optional[list[str]] = None
    legal_entity_id: Optional[int] = None
    subject_line: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class TemplateOut(ORMModel):
    id: int
    title: str
    body: str
    request_types: list[str] = Field(default_factory=list)
    subject_types: list[str] = Field(default_factory=list)
    requester_kinds: list[str] = Field(default_factory=list)
    legal_entity_id: Optional[int]
    subject_line: str
    placeholders: list[str] = Field(default_factory=list)
    source_filename: str
    notes: str
    is_active: bool
    usage_count: int
    created_at: datetime


# --------------------------------------------------------------------------- #
#  Разовый анализ без регистрации
# --------------------------------------------------------------------------- #

class AnalyzeIn(BaseModel):
    text: str = ""
    subject_line: str = ""
    from_email: str = ""
    inbox_email: str = ""
    use_llm: bool = True
