"""Реестр: фильтры, сортировка, счётчики, выгрузка, шаблоны и драфты."""
import io

import pytest


def test_seeded_registry_has_rows(seeded_client):
    page = seeded_client.get("/api/requests").json()
    assert page["total"] == 10
    assert page["items"][0]["reg_number"]


def test_default_sort_is_by_urgency(seeded_client):
    items = seeded_client.get("/api/requests").json()["items"]
    order = ["OVERDUE", "TODAY", "CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE"]
    ranks = [order.index(i["urgency"]) for i in items]
    assert ranks == sorted(ranks)


@pytest.mark.parametrize("param, value, field", [
    ("subject_type", "EMPLOYEE", "subject_type"),
    ("requester_kind", "RKN", "requester_kind"),
    ("request_type", "ACCESS", "request_type"),
    ("status", "NEW", "status"),
])
def test_single_value_filters(seeded_client, param, value, field):
    items = seeded_client.get(f"/api/requests?{param}={value}").json()["items"]
    assert items
    assert all(i[field] == value for i in items)


def test_filter_by_inbox_email(seeded_client):
    items = seeded_client.get(
        "/api/requests?inbox_email=privacy@romashka.ru").json()["items"]
    assert items
    assert all(i["inbox_email"] == "privacy@romashka.ru" for i in items)


def test_filter_by_type_group_rkn(seeded_client):
    items = seeded_client.get("/api/requests?type_group=RKN").json()["items"]
    assert items
    assert all(i["request_type"].startswith("RKN") for i in items)


def test_filter_by_type_group_non_pd(seeded_client):
    items = seeded_client.get("/api/requests?type_group=NON_PD").json()["items"]
    assert items
    assert all(i["has_red_flag"] for i in items)


def test_filter_by_flags(seeded_client):
    red = seeded_client.get("/api/requests?flag=red").json()["items"]
    blue = seeded_client.get("/api/requests?flag=blue").json()["items"]
    none = seeded_client.get("/api/requests?flag=none").json()["items"]
    assert all(i["has_red_flag"] for i in red)
    assert all(i["has_blue_flag"] for i in blue)
    assert all(not i["has_red_flag"] and not i["has_blue_flag"] for i in none)


def test_multiple_values_are_combined_with_or(seeded_client):
    items = seeded_client.get(
        "/api/requests?subject_type=EMPLOYEE&subject_type=USER").json()["items"]
    assert {i["subject_type"] for i in items} <= {"EMPLOYEE", "USER"}
    assert len(items) >= 2


def test_open_only_hides_finished(seeded_client):
    items = seeded_client.get("/api/requests?open_only=true").json()["items"]
    assert all(i["status"] not in
               {"ANSWERED", "CLOSED", "REJECTED", "NOT_APPLICABLE"} for i in items)


def test_full_text_search(seeded_client):
    items = seeded_client.get("/api/requests?q=Роскомнадзор").json()["items"]
    assert items


def test_facets_match_the_filtered_set(seeded_client):
    page = seeded_client.get("/api/requests?subject_type=EMPLOYEE").json()
    assert sum(page["facets"]["subject_type"].values()) == page["total"]


def test_pagination(seeded_client):
    first = seeded_client.get("/api/requests?page=1&page_size=3").json()
    second = seeded_client.get("/api/requests?page=2&page_size=3").json()
    assert len(first["items"]) == 3
    assert first["total"] == second["total"]
    assert {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]} == set()


def test_stats_summary(seeded_client):
    s = seeded_client.get("/api/requests/stats").json()
    assert s["total"] == 10
    assert s["open"] <= s["total"]
    assert set(s["by_urgency"]) >= {"OVERDUE", "LOW", "NONE"}
    assert s["rkn"] >= 1


def test_csv_export_opens_in_excel(seeded_client):
    r = seeded_client.get("/api/requests/export.csv")
    assert r.status_code == 200
    body = r.content.decode("utf-8")
    assert body.startswith("﻿")           # BOM для Excel
    assert "Рег. номер" in body
    assert body.count("\n") >= 10


# --------------------------------------------------------------------------- #
#  Типовые ответы и драфты
# --------------------------------------------------------------------------- #

def test_templates_are_seeded_with_placeholders(seeded_client):
    templates = seeded_client.get("/api/templates").json()
    assert len(templates) == 5
    assert any(t["placeholders"] for t in templates)


def test_upload_template_file_extracts_placeholders(seeded_client):
    body = "Уважаемый {{ФИО}}! Ваш запрос {{НОМЕР}} рассмотрен. Срок: {{СРОК}}."
    r = seeded_client.post(
        "/api/templates/upload",
        files={"files": ("шаблон.txt", body.encode("utf-8"), "text/plain")},
        data={"request_types": "ACCESS"},
    )
    assert r.status_code == 201, r.text
    created = r.json()[0]
    assert created["placeholders"] == ["ФИО", "НОМЕР", "СРОК"]
    assert created["request_types"] == ["ACCESS"]


def test_upload_template_with_unknown_type_is_rejected(seeded_client):
    r = seeded_client.post(
        "/api/templates/upload",
        files={"files": ("ш.txt", b"text", "text/plain")},
        data={"request_types": "НЕТ_ТАКОГО"},
    )
    assert r.status_code == 422


def test_draft_uses_matching_template_and_fills_placeholders(seeded_client):
    access = seeded_client.get("/api/requests?request_type=ACCESS").json()["items"][0]
    detail = seeded_client.get(f"/api/requests/{access['id']}").json()
    assert detail["template_matches"], "должен быть подобран типовой ответ"

    draft = seeded_client.post(f"/api/requests/{access['id']}/draft",
                               json={"use_llm": False}).json()
    assert draft["generator"] == "TEMPLATE"
    assert draft["template_id"] == detail["template_matches"][0]["template_id"]
    assert access["reg_number"] in draft["body"]
    assert "{{" not in draft["body"]           # плейсхолдеры разрешены или помечены
    assert draft["checklist"]


def test_draft_reports_unresolved_placeholders(seeded_client):
    created = seeded_client.post("/api/templates", json={
        "title": "С неизвестным полем",
        "request_types": ["STOP_MARKETING"],
        "body": "Уважаемый {{ФИО}}, отдел {{ПОДРАЗДЕЛЕНИЕ}} уведомлён.",
    }).json()
    req = seeded_client.post("/api/requests", json={
        "body_text": "Прекратите рекламную рассылку, не желаю получать рекламу.",
        "requester_name": "Иванов И. И.", "use_llm": False}).json()
    draft = seeded_client.post(f"/api/requests/{req['id']}/draft",
                               json={"template_id": created["id"], "use_llm": False}).json()
    assert draft["unresolved_placeholders"] == ["ПОДРАЗДЕЛЕНИЕ"]
    assert "[ПОДРАЗДЕЛЕНИЕ]" in draft["body"]
    assert "Иванов И. И." in draft["body"]


def test_draft_falls_back_when_no_template_matches(client):
    req = client.post("/api/requests", json={
        "body_text": "Возражаю против решения, принятого автоматически алгоритмом "
                     "скоринга. Прошу рассмотреть возражение.",
        "use_llm": False}).json()
    draft = client.post(f"/api/requests/{req['id']}/draft", json={"use_llm": False}).json()
    assert draft["generator"] == "FALLBACK"
    assert draft["body"]
    assert req["reg_number"] in draft["body"]


def test_generating_draft_advances_status(seeded_client):
    item = seeded_client.get("/api/requests?status=NEW").json()["items"][0]
    seeded_client.post(f"/api/requests/{item['id']}/draft", json={"use_llm": False})
    assert seeded_client.get(f"/api/requests/{item['id']}").json()["status"] == "DRAFTED"


def test_draft_can_be_edited(seeded_client):
    item = seeded_client.get("/api/requests").json()["items"][0]
    draft = seeded_client.post(f"/api/requests/{item['id']}/draft",
                               json={"use_llm": False}).json()
    updated = seeded_client.patch(f"/api/drafts/{draft['id']}",
                                  json={"body": "Новый текст", "is_final": True}).json()
    assert updated["body"] == "Новый текст"
    assert updated["is_final"] is True


# --------------------------------------------------------------------------- #
#  Справочники и обслуживание
# --------------------------------------------------------------------------- #

def test_reference_crud(client):
    entity = client.post("/api/legal-entities", json={
        "name": "ООО «Тест»", "short_name": "Тест", "inn": "7700000000",
        "aliases": ["Тест"]}).json()
    inbox = client.post("/api/inboxes", json={
        "email": "t@test.ru", "purpose": "privacy",
        "legal_entity_id": entity["id"]}).json()
    service = client.post("/api/services", json={
        "name": "Тестовый сервис", "code": "TST",
        "keywords": ["тестовый сервис"]}).json()

    assert inbox["legal_entity_id"] == entity["id"]
    assert client.get("/api/services").json()[0]["code"] == "TST"

    client.delete(f"/api/services/{service['id']}")
    assert client.get("/api/services?include_inactive=false").json() == []


def test_inbox_binds_legal_entity_to_new_request(client):
    entity = client.post("/api/legal-entities", json={"name": "ООО «Привязка»"}).json()
    client.post("/api/inboxes", json={"email": "bind@test.ru",
                                      "legal_entity_id": entity["id"]})
    created = client.post("/api/requests", json={
        "inbox_email": "bind@test.ru", "body_text": "Прошу удалить мои персональные данные.",
        "use_llm": False}).json()
    assert created["legal_entity_id"] == entity["id"]


def test_service_is_matched_by_keywords(seeded_client):
    created = seeded_client.post("/api/requests", json={
        "body_text": "Прошу удалить мои персональные данные из личного кабинета "
                     "в мобильном приложении.",
        "use_llm": False}).json()
    assert created["service_id"]
    assert "приложение" in created["service_mentioned"].lower()


def test_recalculate_endpoint(seeded_client):
    assert seeded_client.post("/api/maintenance/recalculate").json()["recalculated"] == 10


def test_deleting_request_removes_it(seeded_client):
    item = seeded_client.get("/api/requests").json()["items"][0]
    assert seeded_client.delete(f"/api/requests/{item['id']}").status_code == 204
    assert seeded_client.get(f"/api/requests/{item['id']}").status_code == 404
