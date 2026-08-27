"""
Опциональный ИИ-слой поверх правил.

Принцип: правила — основа, ИИ — уточнение. Если ключа нет, сети нет или модель
ответила мусором, система продолжает работать на детерминированной логике.
ИИ никогда не переписывает срок: он может уточнить тип обращения, вид субъекта
и предложить формулировки, а дату всегда считает движок сроков.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from .config import settings
from .domain import REQUEST_TYPE_LABELS, RequesterKind, RequestType, SubjectType

log = logging.getLogger(__name__)

_TYPES = ", ".join(t.value for t in RequestType)
_SUBJECTS = ", ".join(s.value for s in SubjectType)
_KINDS = ", ".join(k.value for k in RequesterKind)

ANALYSIS_SYSTEM = f"""Ты — помощник специалиста по защите персональных данных (DPO) \
российской компании. Анализируешь входящие обращения субъектов персональных данных \
и письма Роскомнадзора по Федеральному закону № 152-ФЗ «О персональных данных».

Отвечай СТРОГО одним JSON-объектом без markdown-разметки и без пояснений вокруг.

Схема ответа:
{{
  "request_type": "один из: {_TYPES}",
  "secondary_types": ["дополнительные требования в этом же письме"],
  "requester_kind": "один из: {_KINDS}",
  "subject_type": "один из: {_SUBJECTS}",
  "confidence": 0.0-1.0,
  "summary": "1-2 предложения: чего именно требует заявитель",
  "legal_entity_mentioned": "название юрлица, указанное в тексте, или пустая строка",
  "service_mentioned": "сервис/бизнес-процесс, о котором идёт речь, или пустая строка",
  "requester_name": "ФИО заявителя или пустая строка",
  "deadline_in_document": "срок, прямо указанный в документе (ДД.ММ.ГГГГ), или пустая строка",
  "red_flags": [{{"code":"КОД","reason":"почему обращение не относится к персональным данным"}}],
  "blue_flags": [{{"code":"КОД","reason":"в чём именно спорность, со ссылкой на норму"}}],
  "key_points": ["пункты, которые обязательно должны быть в ответе"]
}}

Правила:
- red_flags ставь только когда обращение ОЧЕВИДНО не про персональные данные \
(коммерческое предложение, потребительская претензия о качестве товара, техподдержка, \
резюме, счета, спам).
- blue_flags ставь на всё спорное: неподтверждённые полномочия представителя, \
отсутствие идентификации по ч. 4 ст. 14, конфликт требования об уничтожении со \
сроками хранения, отзыв согласия при наличии иного основания обработки по ч. 1 ст. 6, \
составное обращение, запрос данных третьего лица, неясный адресат-оператор.
- Не выдумывай фактов, которых нет в тексте. Пустая строка лучше догадки.
- Сроки не рассчитывай — их считает система."""


@dataclass
class LlmResult:
    ok: bool
    data: dict
    error: str = ""
    model: str = ""


def available() -> bool:
    return bool(settings.anthropic_api_key) and settings.llm_enabled


def status() -> dict:
    return {
        "enabled": settings.llm_enabled,
        "configured": bool(settings.anthropic_api_key),
        "model": settings.anthropic_model if available() else "",
        "note": (
            "ИИ-разбор активен: уточняет тип обращения и предлагает формулировки."
            if available() else
            "ИИ-разбор выключен. Система работает на детерминированных правилах. "
            "Чтобы включить, задайте ANTHROPIC_API_KEY в .env."
        ),
    }


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key,
                               timeout=float(settings.llm_timeout_s))


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _call(system: str, user: str, max_tokens: int = 3000) -> LlmResult:
    if not available():
        return LlmResult(False, {}, "ИИ-разбор не настроен (нет ANTHROPIC_API_KEY).")
    try:
        resp = _client().messages.create(
            model=settings.anthropic_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return LlmResult(True, {"text": text}, model=settings.anthropic_model)
    except Exception as exc:  # сеть, лимиты, неверный ключ — всё сюда
        log.warning("Обращение к модели не удалось: %s", exc)
        return LlmResult(False, {}, str(exc))


def analyze(*, subject_line: str, body: str, from_email: str, inbox_email: str,
            attachments_text: str = "") -> LlmResult:
    """Уточняющий разбор обращения. Ошибка не критична — вызывающий код это учитывает."""
    user = (
        f"Письмо пришло на ящик оператора: {inbox_email or 'не указан'}\n"
        f"Отправитель: {from_email or 'не указан'}\n"
        f"Тема: {subject_line or '(без темы)'}\n\n"
        f"Текст обращения:\n{body[:20000]}\n"
    )
    if attachments_text:
        user += f"\nТекст вложений (распознан автоматически):\n{attachments_text[:20000]}\n"

    res = _call(ANALYSIS_SYSTEM, user)
    if not res.ok:
        return res
    try:
        return LlmResult(True, _parse_json(res.data["text"]), model=res.model)
    except Exception as exc:
        return LlmResult(False, {}, f"Модель вернула не-JSON: {exc}")


DRAFT_SYSTEM = """Ты — юрист по защите персональных данных российской компании. \
Готовишь ДРАФТ официального ответа на обращение по ФЗ-152.

Требования к тексту:
- Деловой русский язык, без канцелярских штампов ради штампов.
- Ссылайся на конкретные нормы (часть, статья, закон) там, где это уместно.
- Отвечай ровно на те требования, которые заявлены; ничего не обещай сверх этого.
- НЕ придумывай факты: если нужного сведения нет во входных данных, оставь \
плейсхолдер в квадратных скобках, например [указать перечень систем].
- Сроки и даты бери ТОЛЬКО из блока «Рассчитанные сроки», не вычисляй сам.
- Если основой служит типовой ответ — сохраняй его структуру и формулировки, \
адаптируя под конкретное обращение.

Верни только текст письма, без markdown-заголовков и без комментариев."""


def draft_response(*, context: str, template_body: str = "") -> LlmResult:
    user = context
    if template_body:
        user += f"\n\n=== ТИПОВОЙ ОТВЕТ (основа, сохрани структуру) ===\n{template_body[:15000]}"
    res = _call(DRAFT_SYSTEM, user, max_tokens=4000)
    if res.ok:
        return LlmResult(True, {"body": res.data["text"].strip()}, model=res.model)
    return res


VISION_SYSTEM = """Извлеки весь текст с изображения документа дословно, сохраняя \
структуру (абзацы, реквизиты, подписи, номера). Не переводи, не пересказывай, \
не комментируй. Если текста нет — верни пустую строку."""


def read_image(image_b64: str, media_type: str) -> LlmResult:
    """Резервный путь распознавания, если tesseract недоступен или дал плохой результат."""
    if not available():
        return LlmResult(False, {}, "ИИ-распознавание не настроено.")
    try:
        resp = _client().messages.create(
            model=settings.anthropic_model,
            max_tokens=4000,
            system=VISION_SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": "Извлеки текст."},
            ]}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return LlmResult(True, {"text": text.strip()}, model=settings.anthropic_model)
    except Exception as exc:
        log.warning("Распознавание изображения моделью не удалось: %s", exc)
        return LlmResult(False, {}, str(exc))
