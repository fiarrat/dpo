@echo off
chcp 65001 > nul
title Реестр обращений по персональным данным
rem Запуск двойным кликом. Обходит политику выполнения PowerShell,
rem не меняя её в системе.
cd /d "%~dp0"

set SEED=
if not exist "var\dpo.db" set SEED=-Seed

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %SEED% %*
set CODE=%ERRORLEVEL%

echo.
if %CODE% NEQ 0 (
  echo Запуск не состоялся. Выше написано, что именно помешало.
  echo Если непонятно - сфотографируйте это окно целиком.
) else (
  echo Сервер остановлен. Окно можно закрыть.
)
echo.
pause
