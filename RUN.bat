@echo off
title Reestr obrashcheniy po personalnym dannym
rem Zapusk dvoynym klikom. Obhodit politiku vypolneniya PowerShell,
rem ne menyaya ee v sisteme. Fayl namerenno tolko v ASCII.
cd /d "%~dp0"

set SEED=
if not exist "var\dpo.db" set SEED=-Seed

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %SEED% %*
set CODE=%ERRORLEVEL%

echo.
if %CODE% NEQ 0 (
  echo Zapusk ne sostoyalsya. Vyshe napisano, chto imenno pomeshalo.
  echo Esli neponyatno - sfotografiruyte eto okno celikom.
) else (
  echo Server ostanovlen. Okno mozhno zakryt.
)
echo.
pause
