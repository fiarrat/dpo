#!/usr/bin/env bash
# Запуск реестра обращений: подготовка окружения и старт сервера.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
PORT=${PORT:-8000}
HOST=${HOST:-127.0.0.1}

if [ ! -d .venv ]; then
  echo "→ Создаю виртуальное окружение…"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "→ Проверяю зависимости…"
pip install -q --upgrade pip
pip install -q -r backend/requirements.txt

if ! command -v tesseract >/dev/null 2>&1; then
  echo "⚠  tesseract-ocr не найден: фотографии и сканы распознаваться не будут."
  echo "   Ubuntu/Debian: sudo apt-get install -y tesseract-ocr tesseract-ocr-rus poppler-utils"
  echo "   macOS:         brew install tesseract tesseract-lang poppler"
fi

if [ "${SEED:-}" = "1" ]; then
  echo "→ Наполняю базу демонстрационными данными…"
  (cd backend && python -m app.seed ${RESET:+--reset})
fi

echo "→ Запускаю на http://$HOST:$PORT"
cd backend
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT" ${RELOAD:+--reload}
