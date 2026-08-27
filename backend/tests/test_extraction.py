"""Приведение вложений к тексту: PDF, изображения, docx, письма."""
import email.message
import glob
import io
import shutil

import pytest

from app.extraction import clean_text, decode_bytes, extract, strip_html, tool_status

TEXT = ("ЗАПРОС СУБЪЕКТА ПЕРСОНАЛЬНЫХ ДАННЫХ\n"
        "Я, Иванов Иван Иванович, прошу предоставить сведения\n"
        "о моих персональных данных по статье 14 ФЗ-152.")

FONT = next(iter(glob.glob("/usr/share/fonts/**/DejaVuSans.ttf", recursive=True)), None)
has_ocr = shutil.which("tesseract") is not None
has_poppler = shutil.which("pdftoppm") is not None


def _text_pdf() -> bytes:
    reportlab = pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdfmetrics.registerFont(TTFont("DV", FONT))
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("DV", 13)
    y = 780
    for line in TEXT.split("\n"):
        c.drawString(50, y, line)
        y -= 22
    c.save()
    return buf.getvalue()


def _image(fmt: str = "PNG") -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(FONT, 30)
    img = Image.new("RGB", (1100, 220), "white")
    d = ImageDraw.Draw(img)
    y = 20
    for line in TEXT.split("\n"):
        d.text((25, y), line, fill="black", font=font)
        y += 48
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def test_pdf_with_text_layer_uses_text_not_ocr():
    res = extract("запрос.pdf", _text_pdf())
    assert res.method == "PDF_TEXT"
    assert "персональных данных" in res.text.lower()
    assert not res.needs_review


@pytest.mark.skipif(not has_ocr, reason="tesseract не установлен")
def test_photo_is_recognised_in_russian():
    res = extract("фото.png", _image())
    assert res.method == "OCR"
    assert "персональных" in res.text.lower()


@pytest.mark.skipif(not (has_ocr and has_poppler), reason="нужны tesseract и poppler")
def test_scanned_pdf_falls_back_to_ocr():
    res = extract("скан.pdf", _image("PDF"))
    assert res.method == "PDF_OCR"
    assert "персональных" in res.text.lower()


def test_docx_including_tables():
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph(TEXT)
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Договор"
    table.rows[0].cells[1].text = "№ 12/2024"
    buf = io.BytesIO()
    doc.save(buf)
    res = extract("обращение.docx", buf.getvalue())
    assert res.method == "DOCX"
    assert "12/2024" in res.text


def test_eml_in_cp1251_is_decoded():
    msg = email.message.EmailMessage()
    msg["From"] = "ivanov@mail.ru"
    msg["Subject"] = "Запрос"
    msg.set_content(TEXT, charset="cp1251")
    res = extract("письмо.eml", bytes(msg))
    assert res.method == "EML"
    assert "Иванов" in res.text


def test_unsupported_binary_is_reported_not_crashed():
    res = extract("архив.zip", b"PK\x03\x04\x00\x00binary\xff\xfe")
    assert res.needs_review
    assert res.error


def test_html_tags_are_stripped():
    out = strip_html("<html><style>x{}</style><p>Прошу удалить</p><div>данные</div></html>")
    assert "Прошу удалить" in out and "данные" in out
    assert "<" not in out


def test_cp1251_bytes_are_decoded():
    assert "Персональные" in decode_bytes("Персональные данные".encode("cp1251"))


def test_hyphenated_line_breaks_are_joined():
    assert "персональные" in clean_text("персо-\nнальные")


def test_tool_status_reports_capabilities():
    st = tool_status()
    assert set(st) >= {"tesseract", "poppler", "russian_ocr", "pdf_text"}


def test_legacy_doc_format_gets_actionable_message():
    res = extract("обращение.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1binary")
    assert res.needs_review
    assert "DOCX" in res.error


def test_plain_text_file_still_reads():
    res = extract("обращение.txt", TEXT.encode("utf-8"))
    assert res.method == "PLAIN"
    assert "Иванов" in res.text
