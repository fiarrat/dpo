"""Точка входа: FastAPI-приложение и раздача интерфейса."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import analyze, reference, requests as requests_router, templates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("dpo")

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    log.info("База данных: %s", settings.database_url)
    from .extraction import tool_status
    st = tool_status()
    if not st["tesseract"]:
        log.warning("tesseract-ocr не найден — распознавание фотографий и сканов недоступно.")
    elif not st["russian_ocr"]:
        log.warning("Не установлен русский языковой пакет tesseract-ocr-rus.")
    if not st["poppler"]:
        log.warning("poppler-utils не найден — OCR сканированных PDF недоступен.")
    yield


app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Реестр обращений субъектов персональных данных и Роскомнадзора "
        "с контролем сроков по ст. 14, 20 и 21 Федерального закона № 152-ФЗ."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # pragma: no cover
    log.exception("Необработанная ошибка на %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Внутренняя ошибка: {exc}"},
    )


app.include_router(reference.router)
app.include_router(reference.legal_entities_router)
app.include_router(reference.inboxes_router)
app.include_router(reference.services_router)
app.include_router(requests_router.router)
app.include_router(templates.router)
app.include_router(templates.drafts_router)
app.include_router(analyze.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


# --------------------------------------------------------------------------- #
#  Интерфейс
# --------------------------------------------------------------------------- #

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"

if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")
