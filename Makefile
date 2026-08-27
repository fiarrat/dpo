.PHONY: install run seed reset test lint ocr-deps clean

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

install: ## Установить зависимости
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements-dev.txt

ocr-deps: ## Системные пакеты для распознавания фото и сканов (Ubuntu/Debian)
	sudo apt-get update
	sudo apt-get install -y tesseract-ocr tesseract-ocr-rus poppler-utils

run: ## Запустить сервер на http://127.0.0.1:8000
	cd backend && ../$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

seed: ## Наполнить базу демонстрационными данными
	cd backend && ../$(PY) -m app.seed

reset: ## Пересоздать базу с демонстрационными данными
	cd backend && ../$(PY) -m app.seed --reset

test: ## Прогнать тесты
	$(PY) -m pytest backend/tests -q

recalc: ## Пересчитать срочность по всем обращениям (для ежедневного cron)
	curl -fsS -X POST http://127.0.0.1:8000/api/maintenance/recalculate

clean:
	rm -rf var .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
