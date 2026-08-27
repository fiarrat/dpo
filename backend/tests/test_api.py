"""Сквозные проверки API: приём, фильтры, флажки, вложения, драфты."""
import glob
import io
import shutil

import pytest

FONT = next(iter(glob.glob("/usr/share/fonts/**/DejaVuSans.ttf", recursive=True)), None)
has_ocr = shutil.which("tesseract") is not None

ACCESS_BODY = (
    "Я, Иванов Иван Иванович, паспорт 45 09 123456, работаю в ООО «Ромашка» "
    "по трудовому договору № 12/2024. Прошу предоставить сведения о моих "
    "персональных данных в соответствии с частью 7 статьи 14 ФЗ-152."
)


def _photo() -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(FONT, 28)
    img = Image.new("RGB", (1200, 200), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 30), "Прошу уничтожить мои персональные данные", fill="black", font=font)
    d.text((20, 100), "и удалить мою учетную запись.", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_health_and_reference(client):
    assert client.get("/api/health").json()["status"] == "ok"
    ref = client.get("/api/reference").json()
    assert len(ref["request_types"]) >= 20
    assert {"SUBJECT", "RKN", "NON_PD"} <= set(ref["type_groups"])
    assert ref["system"]["calendar"]["years"]
    # Каждый тип обращения должен нести человекочитаемую подпись.
    assert all(t["label"] for t in ref["request_types"])


def test_create_request_classifies_and_sets_deadline(client):
    r = client.post("/api/requests", json={
        "inbox_email": "privacy@romashka.ru",
        "requester_email": "ivanov@mail.ru",
        "subject_line": "Запрос сведений",
        "body_text": ACCESS_BODY,
        "use_llm": False,
    })
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["request_type"] == "ACCESS"
    assert data["subject_type"] == "EMPLOYEE"
    assert data["requester_kind"] == "SUBJECT"
    assert data["due_date"]
    assert data["reg_number"].startswith("ПД-")
    assert data["deadlines"]["primary"]["legal_ref"]


def test_red_flag_for_cooperation_offer(client):
    r = client.post("/api/requests", json={
        "inbox_email": "info@romashka.ru",
        "subject_line": "Коммерческое предложение",
        "body_text": "Наша компания специализируется на CRM. Предлагаем вам сотрудничество.",
        "use_llm": False,
    }).json()
    assert r["has_red_flag"] is True
    assert r["due_date"] is None
    assert any(f["level"] == "RED" for f in r["flags"])


def test_blue_flag_for_representative_without_poa(client):
    r = client.post("/api/requests", json={
        "body_text": "Я, адвокат Соколов, действую от имени Иванова И.И. Прошу "
                     "предоставить сведения о персональных данных доверителя.",
        "use_llm": False,
    }).json()
    assert r["has_blue_flag"] is True
    codes = {f["code"] for f in r["flags"] if f["level"] == "BLUE"}
    assert "REPRESENTATIVE_NO_POA" in codes


def test_upload_pdf_creates_request_with_extracted_text(client):
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdfmetrics.registerFont(TTFont("DV", FONT))
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("DV", 12)
    y = 800
    for chunk in [ACCESS_BODY[i:i + 80] for i in range(0, len(ACCESS_BODY), 80)]:
        c.drawString(40, y, chunk)
        y -= 20
    c.save()

    r = client.post(
        "/api/requests/upload",
        files={"files": ("запрос.pdf", buf.getvalue(), "application/pdf")},
        data={"inbox_email": "privacy@romashka.ru", "use_llm": "false"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["request_type"] == "ACCESS"
    assert data["attachments"][0]["extraction_method"] == "PDF_TEXT"
    assert "персональных данных" in data["body_text"].lower()


@pytest.mark.skipif(not has_ocr, reason="tesseract не установлен")
def test_upload_photo_is_recognised_and_classified(client):
    r = client.post(
        "/api/requests/upload",
        files={"files": ("фото.png", _photo(), "image/png")},
        data={"inbox_email": "privacy@romashka.ru", "use_llm": "false"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["attachments"][0]["extraction_method"] == "OCR"
    assert data["request_type"] == "ERASURE"


def test_upload_without_text_or_files_is_rejected(client):
    r = client.post("/api/requests/upload", data={"body_text": "  "})
    assert r.status_code == 422


def test_manual_type_change_is_not_overwritten_by_reanalysis(client):
    created = client.post("/api/requests", json={
        "body_text": ACCESS_BODY, "use_llm": False}).json()
    patched = client.patch(f"/api/requests/{created['id']}",
                           json={"request_type": "ERASURE"}).json()
    assert patched["request_type"] == "ERASURE"
    assert patched["classified_by"] == "MANUAL"

    again = client.post(f"/api/requests/{created['id']}/reanalyze?use_llm=false").json()
    assert again["request_type"] == "ERASURE"      # ручное решение сохранено

    forced = client.post(
        f"/api/requests/{created['id']}/reanalyze?use_llm=false&overwrite_manual=true"
    ).json()
    assert forced["request_type"] == "ACCESS"


def test_identity_confirmation_moves_the_deadline(client):
    created = client.post("/api/requests", json={
        "body_text": "Прошу предоставить сведения о моих персональных данных.",
        "received_at": "2026-08-03T09:00:00", "use_llm": False}).json()
    before = created["due_date"]
    after = client.patch(f"/api/requests/{created['id']}",
                         json={"identity_confirmed": True}).json()
    assert after["due_date"] > before


def test_manual_due_date_wins_over_calculation(client):
    created = client.post("/api/requests", json={
        "body_text": "Предписание об устранении выявленного нарушения.",
        "requester_email": "office@rkn.gov.ru", "use_llm": False}).json()
    updated = client.patch(f"/api/requests/{created['id']}",
                           json={"manual_due_date": "2026-09-01"}).json()
    assert updated["due_date"] == "2026-09-01"
    assert updated["deadlines"]["primary"]["manual"] is True

    cleared = client.patch(f"/api/requests/{created['id']}",
                           json={"clear_manual_due_date": True}).json()
    assert cleared["due_date"] != "2026-09-01"


def test_status_change_stops_the_clock(client):
    created = client.post("/api/requests", json={
        "body_text": ACCESS_BODY, "received_at": "2026-01-10T09:00:00",
        "use_llm": False}).json()
    assert created["urgency"] == "OVERDUE"
    answered = client.patch(f"/api/requests/{created['id']}",
                            json={"status": "ANSWERED"}).json()
    assert answered["urgency"] == "NONE"
    assert answered["answered_at"]


def test_flags_can_be_added_and_resolved(client):
    created = client.post("/api/requests", json={
        "body_text": ACCESS_BODY, "use_llm": False}).json()
    flag = client.post(f"/api/requests/{created['id']}/flags",
                       json={"level": "BLUE", "code": "CHECK",
                             "reason": "Уточнить объём данных"}).json()
    assert client.get(f"/api/requests/{created['id']}").json()["has_blue_flag"] is True

    client.post(f"/api/requests/{created['id']}/flags/{flag['id']}/resolve",
                json={"resolution": "Уточнено", "resolved_by": "Зимина"})
    detail = client.get(f"/api/requests/{created['id']}").json()
    resolved = [f for f in detail["flags"] if f["id"] == flag["id"]][0]
    assert resolved["resolved_at"]
    assert resolved["resolution"] == "Уточнено"


def test_manual_flag_survives_reanalysis(client):
    created = client.post("/api/requests", json={
        "body_text": ACCESS_BODY, "use_llm": False}).json()
    client.post(f"/api/requests/{created['id']}/flags",
                json={"level": "BLUE", "code": "MY_NOTE", "reason": "Проверить архив"})
    client.post(f"/api/requests/{created['id']}/reanalyze?use_llm=false")
    detail = client.get(f"/api/requests/{created['id']}").json()
    assert "MY_NOTE" in {f["code"] for f in detail["flags"]}


def test_invalid_flag_level_is_rejected(client):
    created = client.post("/api/requests", json={
        "body_text": ACCESS_BODY, "use_llm": False}).json()
    r = client.post(f"/api/requests/{created['id']}/flags",
                    json={"level": "GREEN", "reason": "x"})
    assert r.status_code == 422


def test_invalid_request_type_is_rejected(client):
    created = client.post("/api/requests", json={
        "body_text": ACCESS_BODY, "use_llm": False}).json()
    r = client.patch(f"/api/requests/{created['id']}", json={"request_type": "НЕТ_ТАКОГО"})
    assert r.status_code == 422


def test_attachment_text_can_be_corrected_by_hand(client):
    created = client.post("/api/requests", json={
        "body_text": "Короткое обращение", "use_llm": False}).json()
    att = client.post(
        f"/api/requests/{created['id']}/attachments",
        files={"files": ("скан.txt", b"nonsense", "text/plain")},
        data={"reanalyze_after": "false"},
    ).json()[0]
    fixed = client.patch(
        f"/api/requests/{created['id']}/attachments/{att['id']}",
        data={"text": "Отзываю свое согласие на обработку персональных данных."},
    ).json()
    assert fixed["needs_review"] is False
    detail = client.get(f"/api/requests/{created['id']}").json()
    assert detail["request_type"] == "CONSENT_WITHDRAWAL"


def test_ad_hoc_analysis_does_not_touch_the_registry(client):
    before = client.get("/api/requests").json()["total"]
    result = client.post("/api/analyze", json={"text": ACCESS_BODY, "use_llm": False}).json()
    assert result["classification"]["request_type"] == "ACCESS"
    assert result["deadlines"]["primary"]["due_date"]
    assert client.get("/api/requests").json()["total"] == before


def test_ad_hoc_file_analysis_returns_extracted_text(client):
    r = client.post(
        "/api/analyze/files",
        files={"files": ("обращение.txt", ACCESS_BODY.encode("utf-8"), "text/plain")},
        data={"use_llm": "false"},
    ).json()
    assert r["files"][0]["method"] == "PLAIN"
    assert "персональных данных" in r["extracted_text"].lower()
    assert r["classification"]["request_type"] == "ACCESS"
